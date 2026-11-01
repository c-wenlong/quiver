/* The strip of machines above the Sessions list (#1443): one chip per
 * machine the hub lists (/v1/machines), with how many of its sessions are
 * working and waiting. Clicking a chip filters the list to that machine;
 * clicking it again clears the filter. The strip hides unless there are
 * two or more machines.
 *
 * Ordering and wording follow the Mac's "Other machines" group
 * (OtherMachines.swift, #1436) and the iPhone's MachineStrip.swift: online
 * machines first, then by name; an offline machine reads "last seen 2h".
 * Pure helpers plus one html() view; uses `esc` from app.js at call time. */
"use strict";

const MachineStrip = (() => {
  /* "now", "5m", "2h", "3d": the coarsest whole unit. "" when unknown. */
  function shortAge(epoch, now = Date.now() / 1000) {
    if (!epoch || epoch <= 0) return "";
    const s = Math.max(0, now - epoch);
    if (s < 60) return "now";
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    if (s < 86400) return `${Math.floor(s / 3600)}h`;
    return `${Math.floor(s / 86400)}d`;
  }

  /* "last seen 2h", "last seen just now", or "offline" with no time. */
  function lastSeen(epoch, now = Date.now() / 1000) {
    const age = shortAge(epoch, now);
    if (!age) return "offline";
    return age === "now" ? "last seen just now" : `last seen ${age}`;
  }

  const displayName = (m) => String(m.name || m.id || "").replace(/\.local$/, "");

  /* One chip per machine, online first then by name; [] unless the hub
   * lists two or more. Working is status "active"; waiting is a follow-up
   * or an approval, the sessions that need you. */
  function chips(machines, rows, now = Date.now() / 1000) {
    if (!Array.isArray(machines) || machines.length < 2) return [];
    const working = {}, waiting = {};
    for (const r of rows || []) {
      if (!r.machine_id) continue;
      if (r.status === "active") working[r.machine_id] = (working[r.machine_id] || 0) + 1;
      else if (r.status === "followup" || r.status === "approval") waiting[r.machine_id] = (waiting[r.machine_id] || 0) + 1;
    }
    return machines.map((m) => {
      const online = m.online === true, w = working[m.id] || 0, q = waiting[m.id] || 0;
      return { id: m.id, name: displayName(m), online, working: w, waiting: q,
        detail: online ? `${w} working · ${q} waiting` : lastSeen(m.last_seen_at, now) };
    }).sort((a, b) => {
      if (a.online !== b.online) return a.online ? -1 : 1;
      const byName = a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" });
      return byName || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
    });
  }

  /* Clicking the chosen chip clears the filter; any other chip chooses it. */
  const toggled = (current, id) => (current === id ? "" : id);

  /* The choice, dropped ("") when that machine is no longer listed. */
  const effective = (selected, list) => (list.some((c) => c.id === selected) ? selected : "");

  /* The rows on the chosen machine; every row with no choice. */
  const filter = (rows, machine) => (machine ? rows.filter((r) => r.machine_id === machine) : rows);

  function html(list, selected) {
    if (!list.length) return "";
    return `<div class="machine-strip" role="group" aria-label="Machines">${list.map((c) => `
      <button type="button" class="machine-chip${c.id === selected ? " selected" : ""}" data-machine="${esc(c.id)}"
        aria-pressed="${c.id === selected}" title="${c.id === selected ? "Show every machine" : "Show only this machine's sessions"}">
        <span class="machine-dot${c.online ? " online" : ""}" aria-hidden="true"></span>
        <span class="machine-text"><span class="machine-name">${esc(c.name)}</span>
        <span class="caption">${esc(c.detail)}</span></span>
      </button>`).join("")}</div>`;
  }

  return { shortAge, lastSeen, chips, toggled, effective, filter, html };
})();
