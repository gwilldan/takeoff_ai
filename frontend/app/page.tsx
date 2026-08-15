"use client"
import { AnnotationProvider } from '@/lib/annotations/store';
import { Workspace } from '@/components/workspace/workspace';
import OnlyDesktop from '@/components/only-desktop';
import { useEffect, useState } from 'react';

export default function Page() {

  const [width, setWidth] = useState<number | null>(null);

  useEffect(() => {
    const handleResize = () => setWidth(window.innerWidth);
    handleResize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  if (width === null) {
    return null; 
  }

  if (width < 1024) {
    return <OnlyDesktop />;
  }

  return (
    <AnnotationProvider>
      <Workspace />
    </AnnotationProvider>
  );
}
