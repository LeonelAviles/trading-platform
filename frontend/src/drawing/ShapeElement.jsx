import LineShape from './LineShape';
import HLineShape from './HLineShape';
import RectShape from './RectShape';
import PositionShape from './PositionShape';

export default function ShapeElement(props) {
  const { shape } = props;
  if (shape.type === 'line') return <LineShape {...props} />;
  if (shape.type === 'hline') return <HLineShape {...props} />;
  if (shape.type === 'rect') return <RectShape {...props} />;
  return <PositionShape {...props} />;
}
