import { useLayoutEffect, useRef, useState } from "react";
import type { AgentRunMessage } from "./types";

// One hook instance per conversation; reading history never triggers a jump.
export function useRunChatScroll(messages: AgentRunMessage[], visible: boolean) {
  const viewport = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const scrollTop = useRef(0);
  const previous = useRef<Map<string, string> | null>(null);
  const unseen = useRef(new Set<string>());
  const [unread, setUnread] = useState(0);
  const [atLatest, setAtLatest] = useState(true);
  function clearUnread() { unseen.current.clear(); setUnread(0); }
  function toLatest() {
    following.current = true;
    setAtLatest(true);
    if (viewport.current) {
      viewport.current.scrollTop = viewport.current.scrollHeight;
      scrollTop.current = viewport.current.scrollTop;
    }
    clearUnread();
  }
  function onScroll() {
    const element = viewport.current;
    if (!element) return;
    scrollTop.current = element.scrollTop;
    following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 48;
    setAtLatest(following.current);
    if (following.current) clearUnread();
  }
  useLayoutEffect(() => {
    const next = new Map(messages.map(message => [message.id, JSON.stringify([message.content, message.content_blocks])]));
    const changed = !previous.current || previous.current.size !== next.size || [...next].some(([id, text]) => previous.current?.get(id) !== text);
    if (previous.current) {
      for (const message of messages) {
        if (message.role !== "user" && next.get(message.id) !== previous.current.get(message.id) && (!visible || !following.current)) unseen.current.add(message.id);
      }
      setUnread(unseen.current.size);
    }
    if (messages.length) previous.current = next;
    if (changed && visible && following.current) toLatest();
  }, [messages, visible]);
  useLayoutEffect(() => {
    const element = viewport.current;
    if (!visible || !element) return;
    element.scrollTop = following.current ? element.scrollHeight : scrollTop.current;
    // A short conversation may fit without emitting a scroll event. Reconcile
    // the restored visible position explicitly so already-visible updates do
    // not remain unread. A reader away from the bottom keeps their position.
    onScroll();
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      // Coalesce textarea, streamed Markdown and image resizes. Do not move a
      // reader's anchor just because a polling response rendered again.
      frame = requestAnimationFrame(() => {
        if (following.current) {
          element.scrollTop = element.scrollHeight;
          scrollTop.current = element.scrollTop;
        }
      });
    });
    observer.observe(element);
    if (content.current) observer.observe(content.current);
    return () => { observer.disconnect(); cancelAnimationFrame(frame); };
  }, [visible]);
  return { viewport, content, onScroll, unread, toLatest, atLatest };
}
