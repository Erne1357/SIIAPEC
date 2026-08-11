(() => {
    const API = "/api/v1";
    // Sumidero propio del panel de retención: en archives.html el id "alerts"
    // pertenece a la pestaña de archivos y los avisos salían en la pestaña
    // equivocada.
    const alerts = document.getElementById("alertsRetention");
    const tbody = document.getElementById("tbodyPolicies");
    const form = document.getElementById("formPolicy");
    const polId = document.getElementById("polId");
    const polArchive = document.getElementById("polArchive");
    const polForever = document.getElementById("polForever");
    const polYears = document.getElementById("polYears");
    const polAfter = document.getElementById("polAfter");
    const btnReset = document.getElementById("btnReset");
    const btnDelete = document.getElementById("btnDelete");

    let archives = [];
    let policies = [];

    // Etiquetas en español de los momentos de aplicación de la política.
    const APPLY_AFTER_LABEL = {
        graduated: "Graduación",
        dropped: "Baja o desistimiento",
        enrollment: "Inscripción",
    };

    function flash(msg, type = "success") {
        if (!alerts) return;
        const el = document.createElement("div");
        el.className = `alert alert-${type} alert-dismissible fade show`;
        el.innerHTML =
            `<div></div>` +
            `<button type="button" class="btn-close tap-target" data-bs-dismiss="alert" aria-label="Cerrar aviso"></button>`;
        el.firstElementChild.textContent = msg;
        alerts.prepend(el);
        if (window.SIIAP && typeof window.SIIAP.announce === "function") {
            window.SIIAP.announce(msg);
        }
        setTimeout(() => bootstrap.Alert.getOrCreateInstance(el).close(), 5000);
    }

    function renderArchivesOptions() {
        polArchive.innerHTML = archives
            .map(a => `<option value="${SIIAP.escapeAttr(a.id)}">${SIIAP.escapeHtml(a.name)}</option>`)
            .join("");
    }

    function renderPolicies() {
        if (!policies.length) {
            tbody.innerHTML = `
        <tr>
          <td colspan="5">
            <div class="empty-state empty-state--inline">
              <div class="empty-state__icon"><i class="bi bi-clock-history" aria-hidden="true"></i></div>
              <p class="empty-state__title">Sin políticas de retención</p>
              <p class="empty-state__description">
                Mientras no exista ninguna política, los archivos se conservan indefinidamente.
              </p>
            </div>
          </td>
        </tr>`;
            return;
        }
        tbody.innerHTML = policies.map(p => {
            const a = archives.find(x => x.id === p.archive_id);
            const archiveName = a ? a.name : `Archivo ${p.archive_id}`;
            const applyAfter = APPLY_AFTER_LABEL[p.apply_after] || p.apply_after || "—";
            return `
        <tr data-id="${SIIAP.escapeAttr(p.id)}">
          <th scope="row" class="fw-normal">${SIIAP.escapeHtml(archiveName)}</th>
          <td class="text-center">${p.keep_forever ? "Sí" : "No"}</td>
          <td class="text-center">${p.keep_forever ? "—" : SIIAP.escapeHtml(p.keep_years ?? "—")}</td>
          <td>${SIIAP.escapeHtml(applyAfter)}</td>
          <td class="text-end">
            <button type="button" class="btn btn-sm btn-outline-primary btn-edit"
                    aria-label="Editar la política de ${SIIAP.escapeAttr(archiveName)}">Editar</button>
          </td>
        </tr>
      `;
        }).join("");
    }

    async function loadAll() {
        // archivos (para selector y vista)
        const aRes = await fetch(`${API}/archives?include=step`, { credentials: "same-origin" });
        const aData = await aRes.json();
        archives = aData.items || [];
        renderArchivesOptions();

        // políticas
        const pRes = await fetch(`${API}/retention/policies`, { credentials: "same-origin" });
        const pData = await pRes.json();
        policies = pData.items || [];
        renderPolicies();
    }

    // Delegación sobre el <tbody>, que renderPolicies() nunca reemplaza (solo
    // reescribe su innerHTML), así que basta con enlazarlo una vez.
    tbody.addEventListener("click", (ev) => {
        // closest(): el click puede caer en un hijo del botón, no en el botón.
        const btn = ev.target.closest(".btn-edit");
        if (!btn) return;
        const tr = btn.closest("tr[data-id]");
        if (!tr) return;
        const id = Number(tr.getAttribute("data-id"));
        const p = policies.find(x => x.id === id);
        if (!p) return;
        polId.value = p.id;
        polArchive.value = String(p.archive_id);
        polForever.checked = !!p.keep_forever;
        polYears.value = p.keep_years || "";
        polAfter.value = p.apply_after || "graduated";
        btnDelete.classList.remove("d-none");
        window.scrollTo({ top: 0, behavior: "smooth" });
    });

    form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const body = {
            archive_id: Number(polArchive.value),
            keep_forever: polForever.checked,
            keep_years: polForever.checked ? null : Number(polYears.value || 0),
            apply_after: polAfter.value
        };
        try {
            let res, data;
            if (polId.value) {
                res = await fetch(`${API}/retention/policies/${encodeURIComponent(polId.value)}`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    credentials: "same-origin",
                    body: JSON.stringify(body)
                });
            } else {
                res = await fetch(`${API}/retention/policies`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    credentials: "same-origin",
                    body: JSON.stringify(body)
                });
            }
            data = await res.json();
            if (!res.ok || data.ok === false) throw new Error(data.error || "No se pudo guardar");
            flash("Política guardada");
            btnReset.click();
            await loadAll();
        } catch (err) {
            flash(err.message, "danger");
        }
    });

    btnReset.addEventListener("click", () => {
        polId.value = "";
        polArchive.selectedIndex = 0;
        polForever.checked = false;
        polYears.value = "";
        polAfter.value = "graduated";
        btnDelete.classList.add("d-none");
    });

    btnDelete.addEventListener("click", async () => {
        if (!polId.value) return;
        const ok = await siiapConfirm({
            type: 'danger',
            title: 'Eliminar política',
            message: '¿Eliminar esta política? Esta acción no se puede deshacer.',
            confirmLabel: 'Sí, eliminar',
        });
        if (!ok) return;
        try {
            const res = await fetch(`${API}/retention/policies/${encodeURIComponent(polId.value)}`, {
                method: "DELETE",
                credentials: "same-origin"
            });
            const data = await res.json();
            if (!res.ok || data.ok === false) throw new Error(data.error || "No se pudo eliminar");
            flash("Política eliminada");
            btnReset.click();
            await loadAll();
        } catch (err) {
            flash(err.message, "danger");
        }
    });
    // Consulta de candidatos a eliminación según las políticas vigentes.
    const btnCandidates = document.getElementById("btnCandidates");
    if (btnCandidates) {
        btnCandidates.addEventListener("click", async () => {
            try {
                const res = await fetch("/api/v1/retention/candidates", { credentials: "same-origin" });
                const data = await res.json();
                if (!res.ok || data.ok === false) throw new Error(data.error || "No se pudo traer candidatos");
                const count = data.count || (data.items ? data.items.length : 0);
                flash(
                    count === 1
                        ? "Hay 1 archivo candidato a eliminación."
                        : `Hay ${count} archivos candidatos a eliminación.`,
                    "info"
                );
            } catch (err) {
                flash(err.message, "danger");
            }
        });
    }

    loadAll();
})();