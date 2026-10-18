"""Explicit selected-day S3 preparation with disk limits; never deletes raw data.

Use --days YYYY-MM-DD ... --contract ESU6 --manifest /existing/manifest.json.
Reads existing AWS access. Records actual source IDs, hashes, successes and
failures under DATA_DIR/preparation. No fitting, permission or AWS mutations.
"""
import argparse
from datetime import date
import hashlib
import json
import os
import math
import re
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market.paths import get_paths
from research_ml.schema import MBOFeatureRequest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days',nargs='+',required=True)
    parser.add_argument('--contract',required=True)
    parser.add_argument('--feature-version', choices=['mbo_receive_minute_v1', 'mbo_receive_minute_v2'], default='mbo_receive_minute_v1')
    parser.add_argument('--max-days-per-run', type=int, default=20)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--max-cache-gib',type=float,default=6)
    parser.add_argument('--min-free-gib',type=float,default=20)
    args=parser.parse_args()
    if not re.fullmatch(r'ES[HMUZ][0-9]{1,2}',args.contract): parser.error('explicit ES contract required')
    if not math.isfinite(args.max_cache_gib) or not math.isfinite(args.min_free_gib): parser.error('finite disk limits required')
    days=sorted(set(str(date.fromisoformat(d)) for d in args.days))
    if not 1 <= args.max_days_per_run <= 366:
        parser.error('max-days-per-run must be 1..366')
    if len(days)>args.max_days_per_run or any(date.fromisoformat(d).weekday()>4 for d in days):
        parser.error('selected RTH weekdays exceed the explicit per-run bound')
    from engine.validation import _splits
    allowed=set((_splits('ES') or {}).get('inSample',[]))
    if not set(days)<=allowed: parser.error('selected days must be frozen in-sample research sessions')
    paths=get_paths(); paths.data_dir.mkdir(parents=True,exist_ok=True)
    manifest=json.loads(args.manifest.read_text())['files']
    import boto3
    from botocore.config import Config
    client=boto3.Session(profile_name=os.environ.get('AWS_PROFILE') or None).client('s3',config=Config(connect_timeout=10,read_timeout=60,retries={'max_attempts':2}))
    import databento as db
    from research_ml import feature_store, mbo_source
    audit=paths.data_dir/'preparation'
    if args.feature_version != 'mbo_receive_minute_v1': audit=audit/args.contract/args.feature_version
    audit.mkdir(parents=True,exist_ok=True)
    request={'featureVersion':args.feature_version,'contract':args.contract,'sources':[],'depthLevels':10,'featureWindowSeconds':60,
        'maxBookAgeMs':5000,'maxEventLagMs':5000,'maxUnknownTradeFraction':0.01}
    budget=args.max_cache_gib*2**30; floor=args.min_free_gib*2**30
    if budget<=0 or floor<2**30: parser.error('positive cache cap and at least 1 GiB safety floor required')
    for day in days:
        started=time.time(); record={'day':day,'contract':args.contract,'startedAt':started}
        try:
            key=f'ES/{day}.mbo.dbn.zst'; entry=manifest[key]; bucket,object_key=entry['archive_uri'][5:].split('/',1)
            dst=paths.raw_dir/key
            if not dst.exists():
                h=client.head_object(Bucket=bucket,Key=object_key); size=h['ContentLength']
                if size!=entry['size']: raise ValueError('manifest/object size mismatch')
                used=sum(p.stat().st_size for p in paths.raw_dir.rglob('*') if p.is_file())
                if used+size>budget or shutil.disk_usage(paths.data_dir).free-size<floor:
                    raise ValueError('raw cache cap or free-space floor would be exceeded')
                dst.parent.mkdir(parents=True,exist_ok=True); partial=dst.with_suffix(dst.suffix+'.partial')
                if partial.exists(): raise ValueError('existing partial download requires inspection; not overwritten')
                client.download_file(bucket,object_key,str(partial)); partial.rename(dst)
            if dst.stat().st_size!=entry['size']: raise ValueError('local/archive size mismatch')
            digest=mbo_source.file_hash(dst)
            if entry.get('sha256') and digest!=entry['sha256']: raise ValueError('archive checksum mismatch; preserved for inspection')
            source=None
            for frame in db.DBNStore.from_file(dst).to_df(pretty_ts=False,price_type='fixed',count=100_000):
                identifiers=frame.loc[frame.symbol==args.contract,['instrument_id','publisher_id','channel_id']].drop_duplicates()
                if len(identifiers)==1:
                    i,p,c=map(int,identifiers.iloc[0]); source={'day':day,'instrumentId':i,'publisherId':p,'channelId':c}; break
                if len(identifiers)>1: raise ValueError('ambiguous source identifiers')
                raise ValueError('selected contract absent from opening source chunk; manual inspection required')
            if source is None: raise ValueError('empty source')
            day_request=MBOFeatureRequest.model_validate({**request,'sources':[source]})
            result=feature_store.prepare_day(day_request,day_request.sources[0],0.25)
            request['sources'].append(source)
            checkpoint=audit/'feature-request.json'
            partial_request=checkpoint.with_suffix('.json.partial')
            partial_request.write_text(json.dumps(request,indent=2))
            partial_request.replace(checkpoint)
            record.update(status='complete',source=source,sha256=digest,bytes=dst.stat().st_size,
                checksumScope='archive_expected' if entry.get('sha256') else 'observed_download_hash_no_archive_reference',
                partitionId=result['partitionId'],rows=result['document']['rows'],quality=result['document']['rawSource']['quality'])
        except Exception as exc:
            record.update(status='failed',errorType=type(exc).__name__,reason=str(exc))
            raise
        finally:
            record['seconds']=round(time.time()-started,2)
            with (audit/'attempts.jsonl').open('a') as stream: stream.write(json.dumps(record,allow_nan=False)+'\n')
            print(json.dumps(record,allow_nan=False),flush=True)
    target=audit/'feature-request.json'
    print('request',str(target),flush=True)


if __name__=='__main__': main()
