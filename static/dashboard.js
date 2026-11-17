// Leads dashboard: counts by score/source/service, filterable table, CSV export, auto-reply preview.
(function () {
  let currentScore = "";
  const $ = (id) => document.getElementById(id);

  fetch("/api/status").then((r) => r.json()).then((s) => {
    $("mode").textContent = `${s.sheet} · scoring: ${s.scoring} · auto-reply: ${s.auto_reply} · CRM: ${s.crm}`;
  });

  document.querySelectorAll("#filters button").forEach((btn) =>
    btn.addEventListener("click", () => {
      document.querySelectorAll("#filters button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentScore = btn.dataset.score;
      load();
    })
  );

  function cell(text, className) {
    const td = document.createElement("td");
    td.textContent = text || "";
    if (className) td.className = className;
    return td;
  }

  function fillCounts(tbodyId, counts) {
    $(tbodyId).replaceChildren(...Object.entries(counts).map(([key, n]) => {
      const tr = document.createElement("tr");
      tr.append(cell(key), cell(String(n), "num"));
      return tr;
    }));
  }

  async function load() {
    const query = currentScore ? `?score=${encodeURIComponent(currentScore)}` : "";
    $("csv-link").href = "/api/leads.csv" + query;
    const [all, filtered] = await Promise.all([
      fetch("/api/leads").then((r) => r.json()),
      fetch("/api/leads" + query).then((r) => r.json()),
    ]);
    const s = all.summary;
    $("c-total").textContent = s.total;
    for (const k of ["hot", "warm", "cold"]) $("c-" + k).textContent = s.by_score[k] || 0;
    fillCounts("by-source", s.by_source);
    fillCounts("by-service", s.by_service);

    $("empty").hidden = filtered.leads.length > 0;
    $("leads-body").replaceChildren(...filtered.leads.map((lead) => {
      const tr = document.createElement("tr");
      const score = cell(lead.score, "badge " + (lead.score || ""));
      score.title = "scored by " + (lead.scored_by || "-");
      const reply = document.createElement("td");
      if (lead.auto_reply) {
        const btn = document.createElement("button");
        btn.className = "link";
        btn.textContent = lead.auto_reply.status === "sent" ? "sent - view" : "draft - view";
        btn.addEventListener("click", () => {
          $("reply-subject").textContent = lead.auto_reply.subject;
          $("reply-body").textContent = lead.auto_reply.body + "\n\n(" + lead.auto_reply.drafted_by + ")";
          $("reply-dialog").showModal();
        });
        reply.appendChild(btn);
      }
      tr.append(cell(lead.received_at), cell(lead.name), cell(lead.service), cell(lead.source), score,
                cell(lead.urgency), cell(lead.budget), cell(lead.summary, "summary"), reply);
      return tr;
    }));
  }
  load();
})();
