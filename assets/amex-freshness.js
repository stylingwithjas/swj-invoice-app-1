window.SWJAmex = (() => {
  function uniqueCards(rows) {
    const cards = new Map();
    for (const row of rows) {
      const key = row.mask || row.account_id;
      const previous = cards.get(key);
      const latestBalance = !previous || Date.parse(row.balance_synced_at) >
        (Date.parse(previous.balance_synced_at) || 0) ? row : previous;
      const transactionsSyncedAt = Date.parse(row.transactions_synced_at) >
        (Date.parse(previous?.transactions_synced_at) || 0) ?
        row.transactions_synced_at : previous?.transactions_synced_at;
      cards.set(key, { ...latestBalance, transactions_synced_at: transactionsSyncedAt });
    }
    return [...cards.values()];
  }

  function checkedLabel(value, subject) {
    const time = typeof value === "number" ? value : Date.parse(value);
    if (!Number.isFinite(time)) return `${subject} check pending`;
    const formatted = new Date(time).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'
    });
    const stale = Date.now() - time > 36 * 60 * 60 * 1000;
    return `${subject} checked ${formatted}${stale ? ' · May be out of date' : ''}`;
  }

  function transactionLabel(cards) {
    const times = cards.map(card => Date.parse(card.transactions_synced_at));
    const oldest = times.length ? Math.min(...times) : NaN;
    return checkedLabel(oldest, 'Amex transactions');
  }

  return { uniqueCards, checkedLabel, transactionLabel };
})();
