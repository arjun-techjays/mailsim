// Warns before leaving the builder with unsaved changes: in-app navigation (router blocker)
// and closing/reloading the tab (beforeunload). The draft itself survives in this tab's
// sessionStorage unless the user discards it.
import { useEffect, useRef } from 'react';
import type { Blocker } from 'react-router-dom';
import { useBlocker } from 'react-router-dom';

export function LeaveGuard({ dirty, allowRef, onDiscard }: { dirty: boolean; allowRef: { current: boolean }; onDiscard: () => void }) {
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirtyRef.current && !allowRef.current && currentLocation.pathname !== nextLocation.pathname,
  );

  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [dirty]);

  if (blocker.state !== 'blocked') return null;
  return <LeaveDialog blocker={blocker} onDiscard={onDiscard} />;
}

/** Modal (native <dialog>.showModal(): inert background, focus kept inside, Escape = stay). */
function LeaveDialog({ blocker, onDiscard }: { blocker: Extract<Blocker, { state: 'blocked' }>; onDiscard: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current as HTMLDialogElement;
    if (typeof d.showModal === 'function') d.showModal();
    else d.setAttribute('open', ''); // environments without <dialog> support
    d.querySelector<HTMLButtonElement>('button')?.focus();
  }, []);
  return (
    <dialog
      ref={ref}
      aria-labelledby="leave-h"
      aria-describedby="leave-d"
      className="b-dialog"
      data-testid="leave-dialog"
      onCancel={(e) => {
        e.preventDefault();
        blocker.reset();
      }}
    >
      <h2 id="leave-h">Leave without saving?</h2>
      <p id="leave-d">
        This scenario has unsaved changes. If you leave, the draft stays in this browser tab and is restored when you
        come back, until the tab is closed.
      </p>
      <div className="row">
        <button type="button" onClick={() => blocker.reset()}>
          Stay on this page
        </button>
        <button type="button" onClick={() => blocker.proceed()}>
          Leave (keep the draft)
        </button>
        <button
          type="button"
          onClick={() => {
            onDiscard();
            blocker.proceed();
          }}
        >
          Discard the draft and leave
        </button>
      </div>
    </dialog>
  );
}
