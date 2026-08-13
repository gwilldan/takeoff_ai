import Image from 'next/image';
import workspaceScreenshot from '../public/workspace.png';

export function WorkspacePreview() {
  return (
    <div className="overflow-hidden rounded-2xl border border-rule shadow-[0_28px_70px_-32px_rgba(20,24,28,0.42)]">
      <Image
        src={workspaceScreenshot}
        alt="The Takeoff AI workspace: annotation layers listed on the left with a measured area, a ground floor plan in the centre with one bedroom highlighted as an area annotation, and the sheet thumbnail rail on the right."
        priority
        sizes="(min-width: 1152px) 1088px, 100vw"
        className="block h-auto w-full"
      />
    </div>
  );
}
