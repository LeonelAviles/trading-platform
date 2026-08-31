// Client-side validation of a Strategy Spec v2 against the exported JSON Schema
// (frontend/src/spec/schema.json, written by backend/scripts/export_spec_schema.py).
// The backend's /api/strategies/validate is the authority (it knows the primitive
// registry and RTH); this is the fast path for the editor.
import Ajv2020 from 'ajv/dist/2020';
import bundle from './schema.json';

let validator = null;

function ajv() {
  if (!validator) {
    const inst = new Ajv2020({ allErrors: true, strict: false, allowUnionTypes: true });
    validator = inst.compile(bundle.schema);
  }
  return validator;
}

export const PRIMITIVES = bundle.primitives;
export const OPERATORS = bundle.operators;
export const TIMEFRAMES = bundle.timeframes;

export function parseSpec(text) {
  try {
    return { spec: JSON.parse(text), error: null };
  } catch (e) {
    return { spec: null, error: `JSON: ${e.message}` };
  }
}

// -> { valid, errors: [string] }
export function validateSpec(spec) {
  const v = ajv();
  const ok = v(spec);
  const errors = ok ? [] : (v.errors || []).map((e) => {
    const path = (e.instancePath || '').replace(/^\//, '').replace(/\//g, '.') || 'spec';
    if (e.keyword === 'additionalProperties') return `${path}: unknown field '${e.params.additionalProperty}'`;
    if (e.keyword === 'enum') return `${path}: must be one of ${e.params.allowedValues.join(', ')}`;
    return `${path}: ${e.message}`;
  });
  // Primitive names in the expression tree (the schema types them as free objects).
  const known = new Set(PRIMITIVES.map((p) => p.name));
  const walk = (node, path) => {
    if (!node || typeof node !== 'object') return;
    if (node.ind !== undefined && !known.has(node.ind)) errors.push(`${path}: unknown primitive '${node.ind}'`);
    if (node.op !== undefined && !OPERATORS.includes(node.op)) errors.push(`${path}: unknown operator '${node.op}'`);
    (node.args || []).forEach((a, i) => walk(a, `${path}.${node.op}[${i}]`));
  };
  if (spec?.entry?.trigger) walk(spec.entry.trigger, 'entry.trigger');
  (spec?.entry?.sequence || []).forEach((s, i) => walk(s.when, `entry.sequence[${i}].when`));
  (spec?.filters || []).forEach((f, i) => walk(f, `filters[${i}]`));
  return { valid: errors.length === 0, errors: [...new Set(errors)] };
}
