import { createContext } from 'react';

// What the assistant should assume the trader is looking at. Pages publish
// into it (see CandlestickPage) and ChatPanel sends it as the `context` of
// every turn, so answers are grounded in the chart/backtest on screen without
// the trader having to name ids.
//   { symbol, interval, backtestId, strategyId, backtestStatus, reviewJobId }
// `reviewJobId` is set only for a backtest the trader just launched from the
// builder — it's what tells the panel to open the conversation itself.
export const ChatContext = createContext({ chatContext: {}, setChatContext: () => {} });
