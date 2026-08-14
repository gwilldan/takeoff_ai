import { AnnotationProvider } from '../lib/annotations/store';
import { Workspace } from '../components/workspace/workspace';

export default function Page() {
  return (
    <AnnotationProvider>
      <Workspace />
    </AnnotationProvider>
  );
}
