// Minimal ABIs: only what the UI uses.
// TronWeb supports standard ABI fragments.

export const TreasuryVaultV2_ABI = [
  { "inputs":[{"internalType":"address","name":"admin","type":"address"},{"internalType":"address","name":"guardian","type":"address"}],"stateMutability":"nonpayable","type":"constructor" },
  { "stateMutability":"payable","type":"receive" },

  { "inputs":[],"name":"paused","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function" },
  { "inputs":[],"name":"trxBalance","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function" },
  { "inputs":[{"internalType":"address","name":"token","type":"address"}],"name":"tokenBalance","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function" },

  { "inputs":[{"internalType":"bytes32","name":"role","type":"bytes32"},{"internalType":"address","name":"account","type":"address"}],"name":"hasRole","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function" },

  // Admin / guardian (optional UI hooks)
  { "inputs":[],"name":"pause","outputs":[],"stateMutability":"nonpayable","type":"function" },
  { "inputs":[],"name":"unpause","outputs":[],"stateMutability":"nonpayable","type":"function" },
  { "inputs":[{"internalType":"address payable","name":"to","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"emergencySweepTRX","outputs":[],"stateMutability":"nonpayable","type":"function" },
  { "inputs":[{"internalType":"address","name":"token","type":"address"},{"internalType":"address","name":"to","type":"address"},{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"emergencySweepToken","outputs":[],"stateMutability":"nonpayable","type":"function" },
];

export const SubscriptionRegistryTRXV3_ABI = [
  { "inputs":[{"internalType":"address","name":"_owner","type":"address"},{"internalType":"address payable","name":"_treasury","type":"address"},{"internalType":"uint256","name":"_trxUsdPriceE6","type":"uint256"}],"stateMutability":"nonpayable","type":"constructor" },
  { "stateMutability":"payable","type":"receive" },

  { "inputs":[],"name":"owner","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function" },
  { "inputs":[],"name":"treasury","outputs":[{"internalType":"address payable","name":"","type":"address"}],"stateMutability":"view","type":"function" },
  { "inputs":[],"name":"paused","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function" },

  { "inputs":[],"name":"trxUsdPriceE6","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function" },
  { "inputs":[],"name":"monthlyPriceUsdE6","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function" },
  { "inputs":[{"internalType":"address","name":"user","type":"address"}],"name":"accessUntil","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function" },

  { "inputs":[{"internalType":"address","name":"user","type":"address"}],"name":"isActive","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function" },
  { "inputs":[{"internalType":"uint256","name":"months","type":"uint256"}],"name":"requiredTRXSun","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function" },

  { "inputs":[{"internalType":"uint256","name":"months","type":"uint256"}],"name":"subscribeTRX","outputs":[],"stateMutability":"payable","type":"function" },
  { "inputs":[{"internalType":"address","name":"beneficiary","type":"address"},{"internalType":"uint256","name":"months","type":"uint256"}],"name":"subscribeTRXFor","outputs":[],"stateMutability":"payable","type":"function" },

  // owner/admin ops (optional UI hooks)
  { "inputs":[{"internalType":"address payable","name":"newTreasury","type":"address"}],"name":"setTreasury","outputs":[],"stateMutability":"nonpayable","type":"function" },
  { "inputs":[{"internalType":"uint256","name":"newPrice","type":"uint256"}],"name":"setTrxUsdPriceE6","outputs":[],"stateMutability":"nonpayable","type":"function" },
  { "inputs":[{"internalType":"uint256","name":"newMonthly","type":"uint256"}],"name":"setMonthlyPriceUsdE6","outputs":[],"stateMutability":"nonpayable","type":"function" },
  { "inputs":[],"name":"pause","outputs":[],"stateMutability":"nonpayable","type":"function" },
  { "inputs":[],"name":"unpause","outputs":[],"stateMutability":"nonpayable","type":"function" },
  { "inputs":[{"internalType":"address","name":"newOwner","type":"address"}],"name":"transferOwnership","outputs":[],"stateMutability":"nonpayable","type":"function" },
];

export const BudgetModuleV3_ABI = [
  { "inputs":[{"internalType":"address","name":"_treasury","type":"address"},{"internalType":"address","name":"_payee","type":"address"},{"internalType":"address","name":"_token","type":"address"},{"internalType":"uint256","name":"_capPerPeriod","type":"uint256"},{"internalType":"uint256","name":"_periodSeconds","type":"uint256"}],"stateMutability":"nonpayable","type":"constructor" },

  { "inputs":[],"name":"treasury","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function" },
  { "inputs":[],"name":"payee","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function" },
  { "inputs":[],"name":"token","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function" },

  { "inputs":[],"name":"capPerPeriod","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function" },
  { "inputs":[],"name":"periodSeconds","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function" },
  { "inputs":[],"name":"epochStart","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function" },
  { "inputs":[],"name":"spentThisEpoch","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function" },

  { "inputs":[],"name":"paused","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function" },
  { "inputs":[{"internalType":"uint256","name":"amount","type":"uint256"}],"name":"claim","outputs":[],"stateMutability":"nonpayable","type":"function" },
];