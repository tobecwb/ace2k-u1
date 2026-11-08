import { options } from 'preact';
import { render as renderToString } from 'preact-render-to-string';

// The string renderer runs a class's getDerivedStateFromError only when Preact's options ask for it.
// The browser's renderer always does, so the component tests render error boundaries as the page does.
/** @type {typeof options & { errorBoundaries?: boolean }} */ (options).errorBoundaries = true;

// A component tree as HTML text, for the component tests.
/** @param {import('preact').VNode} vnode @returns {string} */
export const renderHtml = (vnode) => renderToString(vnode);

// The text of an HTML string without tags or repeated spaces, for assertions on wording.
/** @param {string} markup @returns {string} */
export const textOf = (markup) =>
  markup
    .replace(/<[^>]*>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

// The markup of a component, and every element vnode it made, so a test can press its buttons.
/** @param {import('preact').VNode} vnode @returns {{ out: string, nodes: any[] }} */
export function rendered(vnode) {
  /** @type {any[]} */
  const nodes = [];
  const prev = options.vnode;
  options.vnode = (v) => {
    if (typeof v.type === 'string') nodes.push(v);
    prev?.(v);
  };
  try {
    return { out: renderHtml(vnode), nodes };
  } finally {
    options.vnode = prev;
  }
}

// The text of a vnode (or a list of them), with its children's text.
/** @param {any} v @returns {string} */
export const textIn = (v) => {
  if (Array.isArray(v)) return v.map(textIn).join('');
  if (!v || typeof v !== 'object') return String(v ?? '');
  return textIn(v.props.children);
};

// The button vnodes among nodes.
/** @param {any[]} nodes @returns {any[]} */
export const buttons = (nodes) => nodes.filter((n) => n.type === 'button');
