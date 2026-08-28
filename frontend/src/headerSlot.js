import { createContext } from 'react';

// DOM nodes of the top-bar's portal slots. Pages portal route-specific
// controls into them (see App.jsx) so there's one unified header:
//   leading  — the identity block at the far left. Owned by the page, not the
//              shell: on a review that's the strategy + symbol being reviewed,
//              which is read-only because it comes from the route, not a picker.
//   main     — the flexible middle region (before the AI Assist button)
//   trailing — after the AI Assist button (e.g. the chart's settings gear)
export const HeaderSlotContext = createContext({ leading: null, main: null, trailing: null });
