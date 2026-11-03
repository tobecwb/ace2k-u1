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
