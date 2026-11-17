// Lead form: submits to /api/leads, shows field errors, and (in demo mode) the latest CSV rows.
(function () {
  const form = document.getElementById("lead-form");
  const status = document.getElementById("form-status");
  const button = form.querySelector("button");

  fetch("/api/status")
    .then((r) => r.json())
    .then((s) => {
      document.getElementById("mode").textContent = `${s.sheet} → ${s.notifiers.join(", ")}`;
    });
  loadLeads();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearErrors();
    button.disabled = true;
    status.className = "status";
    status.textContent = "Sending...";
    try {
      const res = await fetch("/api/leads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      const data = await res.json();
      if (data.ok) {
        status.className = "status ok";
        status.textContent = data.message;
        form.reset();
        loadLeads();
      } else {
        showErrors(data.errors || {});
      }
    } catch (err) {
      status.className = "status err";
      status.textContent = "Network error - please try again.";
    } finally {
      button.disabled = false;
    }
  });

  function showErrors(errors) {
    status.className = "status err";
    status.textContent = errors.form || "Please check the highlighted fields.";
    for (const [field, message] of Object.entries(errors)) {
      const input = form.elements[field];
      if (!input || field === "form") continue;
      input.classList.add("invalid");
      const hint = document.createElement("span");
      hint.className = "field-error";
      hint.textContent = message;
      input.closest("label").appendChild(hint);
    }
  }

  function clearErrors() {
    form.querySelectorAll(".invalid").forEach((el) => el.classList.remove("invalid"));
    form.querySelectorAll(".field-error").forEach((el) => el.remove());
  }

  // Demo mode only: the server returns rows from the local CSV, never from a real sheet.
  async function loadLeads() {
    const { leads } = await fetch("/api/leads").then((r) => r.json());
    const panel = document.getElementById("leads-panel");
    const body = document.getElementById("leads-body");
    panel.hidden = leads.length === 0;
    body.replaceChildren(
      ...leads.reverse().map((lead) => {
        const tr = document.createElement("tr");
        for (const key of ["received_at", "name", "email", "service", "message"]) {
          const td = document.createElement("td");
          td.textContent = lead[key];
          tr.appendChild(td);
        }
        return tr;
      })
    );
  }

  // Presentation mode: /?autoplay fills in a sample (fictional) request and submits it.
  if (new URLSearchParams(location.search).has("autoplay")) {
    const sample = {
      name: "Jordan Example", email: `jordan+${Date.now() % 100000}@example.com`, phone: "+1 555 0100",
      service: "Patio & paving", message: "We'd like a quote for a 4 x 5 m stone patio behind the house.",
    };
    for (const [k, v] of Object.entries(sample)) form.elements[k].value = v;
    form.requestSubmit();
  }
})();
