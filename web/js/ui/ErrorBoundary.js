import { Component, html } from './html.js';
import { t } from '../i18n.js';

/** @typedef {{ children?: import('preact').ComponentChildren }} BoundaryProps */
/** @typedef {{ error?: Error }} BoundaryState */

// A part of the page that throws shows its error in place; the rest of the page keeps working.
// Try again clears the error, so the part is drawn again from the current state.
/** @extends {Component<BoundaryProps, BoundaryState>} */
export class ErrorBoundary extends Component {
  /** @param {Error} error @returns {BoundaryState} */
  static getDerivedStateFromError(error) {
    return { error };
  }

  /** @param {BoundaryProps} props @param {BoundaryState} state */
  render({ children }, { error }) {
    if (!error) return children;
    return html`<div class="broken">
      <span>${t('ui.broken', { msg: error.message })}</span>
      <button type="button" class="btn" onClick=${() => this.setState({ error: undefined })}>${t('ui.retry')}</button>
    </div>`;
  }
}
