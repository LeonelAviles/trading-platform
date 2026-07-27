import { createContext } from 'react';

// Holds the DOM node of the single top-bar's tool slot. Pages portal their
// route-specific controls into it (see App.jsx) so there's one unified header
// instead of a global bar stacked on top of a per-page toolbar.
export const HeaderSlotContext = createContext(null);
