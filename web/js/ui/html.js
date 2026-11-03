// The one place the components import Preact from. The browser resolves the names through the
// import map in index.html (web/vendor/); node resolves them from web/node_modules.
export { html, render } from 'htm/preact';
export { Component } from 'preact';
export { useState, useEffect, useLayoutEffect, useRef, useMemo } from 'preact/hooks';
