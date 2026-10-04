import { useEffect, type RefObject } from "react";

export function useDismissiblePopover(
  open: boolean,
  setOpen: (open: boolean) => void,
  rootRef: RefObject<HTMLElement | null>,
  triggerRef: RefObject<HTMLElement | null>
) {
  useEffect(() => {
    if (!open) return;
    function handlePointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented) return;
      // The innermost open picker owns Escape and its focus loop.
      const nestedOpen = Array.from(rootRef.current?.querySelectorAll<HTMLElement>('[aria-haspopup][aria-expanded="true"]') ?? [])
        .some(element => element !== triggerRef.current && element.getClientRects().length > 0);
      if (nestedOpen) return;
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        triggerRef.current?.focus();
        return;
      }
      if (event.key !== "Tab" || !rootRef.current?.contains(document.activeElement)) return;
      const focusRoot = rootRef.current.querySelector<HTMLElement>('[role="dialog"][aria-modal="true"]') ?? rootRef.current;
      const focusable = Array.from(focusRoot.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'))
        .filter(element => element.getClientRects().length > 0);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, rootRef, setOpen, triggerRef]);
}
