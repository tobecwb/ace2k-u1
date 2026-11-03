// The page's draw, made safe to call from inside a draw. An effect that runs during a draw may call an
// action that draws again. Drawing into the same root while it is being drawn mounts a second tree, so a
// call made during a draw only marks it dirty, and one more draw follows on a microtask.
// A chain of draws that each ask for another is cut after MAX_CHAIN, so it cannot starve the page. The draw asked
// for past the cap is not lost: one last draw follows on a later task, outside the chain, so the page shows the
// latest state. A draw asked for during that last one is dropped, so a draw that always asks for another stops
// there. The next draw that nothing asked for starts a new chain.

export const MAX_CHAIN = 10;

// Returns the render function for mount, the function that draws the page once.
/** @param {() => void} mount @returns {() => void} */
export function makeRenderer(mount) {
  let busy = false;
  let dirty = false;
  let chain = 0;
  let warned = false;
  // The last draw of a chain cut at the cap is scheduled (or done): no other one is.
  let last = false;
  // A draw asked for once the chain is at the cap: warned once, and one last draw on a later task.
  /** @returns {void} */
  function capped() {
    if (!warned) {
      warned = true;
      console.warn(`render: stopped after ${MAX_CHAIN} draws in a row that each asked for another`);
    }
    if (!last) {
      last = true;
      setTimeout(draw, 0);
    }
  }
  /** @returns {void} */
  function draw() {
    if (busy) {
      if (dirty) return;
      if (chain >= MAX_CHAIN) {
        capped();
        return;
      }
      dirty = true;
      queueMicrotask(() => {
        dirty = false;
        chain += 1;
        draw();
      });
      return;
    }
    busy = true;
    try {
      mount();
    } finally {
      busy = false;
    }
  }
  return () => {
    // A draw that nothing asked for starts a new chain.
    if (!busy && !dirty) {
      chain = 0;
      last = false;
    }
    draw();
  };
}
