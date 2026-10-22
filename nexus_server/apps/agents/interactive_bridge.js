/* SDK bridge for an opaque-origin, sandboxed custom view. No credentials here. */
(() => {
  let port, state = {}, listeners = [], counter = 0;
  const pending = new Map();
  Object.defineProperty(window, 'nexus', {value: Object.freeze({
    get state() { return state; },
    onState(fn) { listeners.push(fn); fn(state); return () => { listeners = listeners.filter(x => x !== fn); }; },
    action(name, args = {}) {
      return new Promise((resolve, reject) => {
        if (!port) { reject(new Error('View is still connecting.')); return; }
        const id = String(++counter);
        const timer = setTimeout(() => { pending.delete(id); reject(new Error('Action timed out; check Agent state before retrying.')); }, 130000);
        pending.set(id, {resolve, reject, timer});
        port.postMessage({type: 'action', id, name, arguments: args});
      });
    }
  }), writable: false});
  window.addEventListener('message', e => {
    if (e.source !== parent || e.data?.type !== 'nexus-connect' || !e.ports[0] || port) return;
    port = e.ports[0];
    port.onmessage = ({data}) => {
      if (data.type === 'state') {
        state = data.state;
        for (const listener of listeners) { try { listener(state); } catch (_) {} }
      } else if (data.type === 'result') {
        const p = pending.get(data.id); if (!p) return;
        pending.delete(data.id); clearTimeout(p.timer);
        if (data.error) p.reject(new Error(data.error)); else p.resolve(data.result);
      }
    };
    port.postMessage({type: 'ready'});
  });
})();
