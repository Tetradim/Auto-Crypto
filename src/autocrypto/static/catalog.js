(function () {
  "use strict";

  const defaultMarkets = [
    { symbol: "BTC/USDT", compact: "BTCUSDT", price: 66234.13, change: 3.15 },
    { symbol: "ETH/USDT", compact: "ETHUSDT", price: 3421.9, change: 1.42 },
    { symbol: "SOL/USDT", compact: "SOLUSDT", price: 148.26, change: -0.68 },
    { symbol: "USDC Drift", compact: "USDC", price: 0.04, change: 0, suffix: "%" },
  ];

  const strategies = [
    {
      id: "breakout-guard",
      name: "Breakout Guard",
      type: "signal",
      pair: "BTCUSDT",
      amount: "250",
      price: "66234",
      stop: "2",
      takeProfit: "4.5",
      trailingStop: "2.5",
      roi: "+229.35%",
      win: "86.44%",
      drawdown: "8.2%",
    },
    {
      id: "mean-grid-18",
      name: "Mean Grid 18",
      type: "grid",
      pair: "ETHUSDT",
      amount: "150",
      price: "3421",
      stop: "2.5",
      takeProfit: "5",
      trailingStop: "3",
      roi: "+110.11%",
      win: "74.93%",
      drawdown: "5.9%",
    },
    {
      id: "dca-ladder",
      name: "DCA Ladder",
      type: "dca",
      pair: "SOLUSDT",
      amount: "90",
      price: "148",
      stop: "3",
      takeProfit: "7",
      trailingStop: "4",
      roi: "+64.28%",
      win: "80.95%",
      drawdown: "6.7%",
    },
  ];

  window.AutoCryptoCatalog = { defaultMarkets, strategies };
})();
