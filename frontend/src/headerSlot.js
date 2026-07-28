import { createContext } from 'react';

// DOM nodes of the top-bar's portal slots. Pages portal route-specific
// controls into them (see App.jsx) so there's one unified header:
//   main     — the flexible middle region (before the AI Assist button)
//   trailing — after the AI Assist button (e.g. the chart's settings gear)
export const HeaderSlotContext = createContext({ main: null, trailing: null });
