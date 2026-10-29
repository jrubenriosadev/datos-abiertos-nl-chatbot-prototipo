/* ==========================================================
   Widget de chat para el prototipo de Datos Abiertos.
   Solo interfaz: manda cada pregunta al backend (POST apiUrl)
   y pinta la respuesta. No tiene lógica de datos ni claves.

   Uso:
     <script>window.DatosChatConfig = { apiUrl: "http://localhost:8000/api/chat" };</script>
     <script src="chat-widget.js" defer></script>

   API pública:
     window.DatosChat.open()   abre la ventana
     window.DatosChat.close()  la cierra
     window.DatosChat.ask(t)   abre la ventana y envía el texto t

   Contrato con el backend (ver frontend/README.md):
     Petición:  { "mensaje": "...", "sesion": "uuid" }
     Respuesta: { "respuesta": "texto", "grafica": {...} | null, "fuente": "texto" | null,
                  "avisos": ["texto", ...] (opcional), "tipo": "apoyo" (opcional),
                  "contactos": [{ "nombre": "...", "telefono": "...", "detalle": "..." }] (opcional) }
   ========================================================== */
(function () {
  "use strict";

  var defaults = {
    apiUrl: "http://localhost:8000/api/chat",
    title: "Asistente de datos",
    subtitle: "Pregunta sobre consultas de salud en Nuevo León",
    avatarUrl: null, // Ej.: "../assets/mascota.png". Si es null, se usa un ícono.
    welcome:
      "Hola. Puedo ayudarte a encontrar y entender los datos abiertos de consultas de salud en Nuevo León.\n\nEscribe tu pregunta con tus propias palabras.",
    examples: [
      "¿Por qué motivos se consulta más en Monterrey?",
      "¿Cuántas consultas hubo cada mes?",
      "¿Qué enfermedades son más comunes en personas de 60 años o más?"
    ],
    placeholder: "Escribe tu pregunta",
    maxLength: 500,
    timeoutMs: 90000 // el asistente puede hacer varias consultas por pregunta
  };

  var cfg = Object.assign({}, defaults, window.DatosChatConfig || {});

  // ---------- Utilidades ----------
  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "class") node.className = attrs[k];
        else if (k === "text") node.textContent = attrs[k];
        else node.setAttribute(k, attrs[k]);
      });
    }
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "s-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }

  var ICON_CHAT =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/><path d="M8.5 12h.01M12 12h.01M15.5 12h.01"/></svg>';
  var ICON_CLOSE =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true">' +
    '<path d="M6 6l12 12M18 6L6 18"/></svg>';
  var ICON_EXPAND =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M15 4h5v5M9 20H4v-5M20 4l-6 6M4 20l6-6"/></svg>';
  var ICON_SHRINK =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M14 10h5M14 10V5M10 14H5M10 14v5M14 10l6-6M10 14l-6 6"/></svg>';

  function avatar() {
    var box = el("span", { class: "dchat-avatar", "aria-hidden": "true" });
    if (cfg.avatarUrl) {
      box.appendChild(el("img", { src: cfg.avatarUrl, alt: "" }));
    } else {
      box.innerHTML = ICON_CHAT; // contenido fijo, no viene del usuario
    }
    return box;
  }

  // ---------- Estado ----------
  var sessionId = uuid(); // solo en memoria; no se guarda en el navegador
  var busy = false;
  var started = false;

  // ---------- Construcción del DOM ----------
  var root = el("div", { class: "dchat-root" });

  var launcher = el("button", {
    class: "dchat-launcher", type: "button",
    "aria-expanded": "false", "aria-controls": "dchat-panel"
  });
  launcher.appendChild(avatar());
  launcher.appendChild(el("span", { text: "Pregunta a los datos" }));

  var panel = el("section", {
    class: "dchat-panel", id: "dchat-panel", role: "dialog",
    "aria-label": cfg.title, "data-open": "false", "data-size": "normal"
  });

  var closeBtn = el("button", { class: "dchat-close", type: "button", "aria-label": "Cerrar el chat" });
  closeBtn.innerHTML = ICON_CLOSE;

  // Botón para ampliar o reducir la ventana (en celular la ventana ya ocupa toda la pantalla y se oculta).
  var sizeBtn = el("button", {
    class: "dchat-close dchat-size", type: "button", "aria-pressed": "false",
    "aria-label": "Ampliar la ventana", title: "Ampliar la ventana"
  });
  sizeBtn.innerHTML = ICON_EXPAND;
  function setExpanded(state) {
    panel.setAttribute("data-size", state ? "grande" : "normal");
    sizeBtn.setAttribute("aria-pressed", state ? "true" : "false");
    var label = state ? "Reducir la ventana" : "Ampliar la ventana";
    sizeBtn.setAttribute("aria-label", label);
    sizeBtn.setAttribute("title", label);
    sizeBtn.innerHTML = state ? ICON_SHRINK : ICON_EXPAND; // íconos fijos, no vienen del usuario
  }
  sizeBtn.addEventListener("click", function () {
    setExpanded(panel.getAttribute("data-size") !== "grande");
  });

  var header = el("div", { class: "dchat-header" }, [
    avatar(),
    el("div", { class: "dchat-titles" }, [
      el("p", { class: "dchat-title", text: cfg.title }),
      el("p", { class: "dchat-subtitle", text: cfg.subtitle })
    ]),
    sizeBtn,
    closeBtn
  ]);

  var messages = el("div", {
    class: "dchat-messages", role: "log", "aria-live": "polite", "aria-label": "Conversación"
  });

  var suggestions = el("div", { class: "dchat-suggestions" });
  cfg.examples.forEach(function (text) {
    var b = el("button", { class: "dchat-suggestion", type: "button", text: text });
    b.addEventListener("click", function () { send(text); });
    suggestions.appendChild(b);
  });

  var input = el("input", {
    class: "dchat-input", type: "text", name: "mensaje", autocomplete: "off",
    maxlength: String(cfg.maxLength), placeholder: cfg.placeholder, "aria-label": "Tu pregunta"
  });
  var sendBtn = el("button", { class: "dchat-send", type: "submit", text: "Enviar" });
  var form = el("form", { class: "dchat-form" }, [input, sendBtn]);

  var hint = el("p", {
    class: "dchat-hint",
    text: "Este asistente solo responde sobre los datos abiertos disponibles."
  });

  panel.appendChild(header);
  panel.appendChild(messages);
  panel.appendChild(suggestions);
  panel.appendChild(form);
  panel.appendChild(hint);
  root.appendChild(launcher);
  root.appendChild(panel);

  // ---------- Render de mensajes ----------
  // Segunda defensa: ningún marcador interno ([[GRAFICA_AQUI]], {{grafica}}) llega a la pantalla,
  // aunque el backend ya los quita.
  var MARKER = /\[\[[^\[\]\n]{1,80}\]\]|\{\{[^{}\n]{1,80}\}\}/g;
  function stripMarkers(text) {
    return String(text || "").replace(MARKER, "").replace(/[ \t]{2,}/g, " ").replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n").trim();
  }

  // Texto simple y seguro: párrafos y listas con "- ". Nunca se usa innerHTML con texto externo.
  function renderText(container, text) {
    stripMarkers(text).split(/\n{2,}/).forEach(function (block) {
      var lines = block.split("\n").filter(function (l) { return l.trim() !== ""; });
      if (!lines.length) return;
      var isList = lines.every(function (l) { return /^\s*[-•]\s+/.test(l); });
      if (isList) {
        var ul = el("ul");
        lines.forEach(function (l) { ul.appendChild(el("li", { text: l.replace(/^\s*[-•]\s+/, "") })); });
        container.appendChild(ul);
      } else {
        container.appendChild(el("p", { text: lines.join(" ") }));
      }
    });
  }

  function scrollDown() { messages.scrollTop = messages.scrollHeight; }

  function addMessage(kind, text) {
    var msg = el("div", { class: "dchat-msg dchat-msg--" + kind });
    renderText(msg, text);
    messages.appendChild(msg);
    scrollDown();
    return msg;
  }

  function formatNumber(n) {
    if (typeof n !== "number" || !isFinite(n)) return String(n);
    return n.toLocaleString("es-MX");
  }

  // Valor con su unidad; "—" si no hay dato (por ejemplo, un grupo oculto por privacidad).
  function formatValue(v, unit) {
    if (v === null || v === undefined || v === "") return "—";
    var s = formatNumber(Number(v));
    if (!unit) return s;
    return unit === "%" ? s + "%" : s + " " + unit;
  }

  function positive(v) {
    var x = Number(v);
    return isFinite(x) && x > 0 ? x : 0;
  }

  var CHART_TYPES = ["barras", "horizontales", "agrupadas", "apiladas"];
  var MAX_SERIES = 8;
  var MAX_COLUMNS = 8; // con más categorías, las barras verticales no caben y se dibujan horizontales

  /* Ordena y valida la especificación que manda el backend:
     { titulo, tipo, unidad, etiquetas: [...], valores: [...] (una serie)
       o series: [{ nombre, valores: [...] }], marcadas: [índices], nota } */
  function normalizeSpec(spec) {
    var labels = Array.isArray(spec.etiquetas) ? spec.etiquetas.map(String) : [];
    var series;
    if (Array.isArray(spec.series) && spec.series.length) {
      series = spec.series.slice(0, MAX_SERIES).map(function (s) {
        return {
          nombre: String(s && s.nombre != null ? s.nombre : ""),
          valores: Array.isArray(s && s.valores) ? s.valores : []
        };
      });
    } else {
      series = [{ nombre: "", valores: Array.isArray(spec.valores) ? spec.valores : [] }];
    }
    var tipo = CHART_TYPES.indexOf(spec.tipo) >= 0 ? spec.tipo : "horizontales";
    if (series.length === 1 && (tipo === "agrupadas" || tipo === "apiladas")) tipo = "horizontales";
    if (series.length > 1 && (tipo === "barras" || tipo === "horizontales")) tipo = "agrupadas";
    if (tipo === "barras" && labels.length > MAX_COLUMNS) tipo = "horizontales";
    var marked = {};
    (Array.isArray(spec.marcadas) ? spec.marcadas : []).forEach(function (i) { marked[i] = true; });
    return {
      titulo: String(spec.titulo || "Gráfica de barras"),
      tipo: tipo,
      unit: spec.unidad ? String(spec.unidad) : "",
      labels: labels,
      series: series,
      marked: marked,
      nota: spec.nota ? String(spec.nota) : ""
    };
  }

  function valueAt(s, i) { return i < s.valores.length ? s.valores[i] : null; }

  function labelText(c, i) { return c.labels[i] + (c.marked[i] ? " *" : ""); }

  function fillEl(serieIndex, marked, pct, prop) {
    var fill = el("div", { class: "dchat-bar-fill dchat-serie-" + serieIndex + (marked ? " dchat-bar--marcada" : "") });
    fill.style[prop] = Math.max(0, Math.min(100, pct)) + "%";
    return fill;
  }

  // Barras horizontales (una serie): etiqueta | barra | valor
  function drawHorizontal(c, box) {
    var max = Math.max.apply(null, c.series[0].valores.map(positive).concat([0])) || 1;
    c.labels.forEach(function (label, i) {
      var v = valueAt(c.series[0], i);
      box.appendChild(el("div", { class: "dchat-bar-row" }, [
        el("span", { class: "dchat-bar-label", title: label, text: labelText(c, i) }),
        el("div", { class: "dchat-bar-track" }, [fillEl(0, c.marked[i], positive(v) / max * 100, "width")]),
        el("span", { class: "dchat-bar-value", text: formatValue(v, c.unit) })
      ]));
    });
  }

  // Barras verticales (una serie, pocas categorías; útil para meses)
  function drawVertical(c, box) {
    var max = Math.max.apply(null, c.series[0].valores.map(positive).concat([0])) || 1;
    var cols = el("div", { class: "dchat-cols" });
    c.labels.forEach(function (label, i) {
      var v = valueAt(c.series[0], i);
      cols.appendChild(el("div", { class: "dchat-col" }, [
        el("div", { class: "dchat-col-plot" }, [
          el("span", { class: "dchat-col-value", text: formatValue(v, c.unit === "%" ? "%" : "") }),
          fillEl(0, c.marked[i], positive(v) / max * 100, "height")
        ]),
        el("span", { class: "dchat-col-label", title: label, text: labelText(c, i) })
      ]));
    });
    box.appendChild(cols);
  }

  // Barras agrupadas: por categoría, una barra delgada por serie
  function drawGrouped(c, box) {
    var all = [];
    c.series.forEach(function (s) { all = all.concat(s.valores.map(positive)); });
    var max = Math.max.apply(null, all.concat([0])) || 1;
    c.labels.forEach(function (label, i) {
      var group = el("div", { class: "dchat-bar-group" });
      c.series.forEach(function (s, k) {
        var v = valueAt(s, i);
        group.appendChild(el("div", { class: "dchat-bar-sub" }, [
          el("div", { class: "dchat-bar-track dchat-bar-track--thin" }, [fillEl(k, c.marked[i], positive(v) / max * 100, "width")]),
          el("span", { class: "dchat-bar-value", text: formatValue(v, c.unit) })
        ]));
      });
      box.appendChild(el("div", { class: "dchat-bar-row dchat-bar-row--group" }, [
        el("span", { class: "dchat-bar-label", title: label, text: labelText(c, i) }),
        group
      ]));
    });
  }

  // Barras apiladas: una barra por categoría dividida por serie; se muestra el total
  function drawStacked(c, box) {
    var totals = c.labels.map(function (_, i) {
      return c.series.reduce(function (sum, s) { return sum + positive(valueAt(s, i)); }, 0);
    });
    var max = Math.max.apply(null, totals.concat([0])) || 1;
    c.labels.forEach(function (label, i) {
      var track = el("div", { class: "dchat-bar-track dchat-bar-track--stack" });
      c.series.forEach(function (s, k) {
        track.appendChild(fillEl(k, c.marked[i], positive(valueAt(s, i)) / max * 100, "width"));
      });
      box.appendChild(el("div", { class: "dchat-bar-row" }, [
        el("span", { class: "dchat-bar-label", title: label, text: labelText(c, i) }),
        track,
        el("span", { class: "dchat-bar-value", text: formatValue(totals[i], c.unit) })
      ]));
    });
  }

  /* Gráfica de barras sin librerías: verticales, horizontales, agrupadas o apiladas.
     Siempre incluye una tabla accesible con los mismos datos. */
  function renderBarChart(spec) {
    var c = normalizeSpec(spec || {});
    if (!c.labels.length) return null;
    var multi = c.series.length > 1;

    var fig = el("figure", { class: "dchat-chart" });
    fig.appendChild(el("figcaption", { text: c.titulo }));

    if (multi) {
      var legend = el("ul", { class: "dchat-legend", "aria-hidden": "true" });
      c.series.forEach(function (s, k) {
        legend.appendChild(el("li", null, [el("span", { class: "dchat-swatch dchat-serie-" + k }), el("span", { text: s.nombre })]));
      });
      fig.appendChild(legend);
    }

    var box = el("div", {
      class: "dchat-bars", role: "img",
      "aria-label": c.titulo + ". Hay una tabla con los valores justo debajo."
    });
    if (c.tipo === "barras") drawVertical(c, box);
    else if (c.tipo === "agrupadas") drawGrouped(c, box);
    else if (c.tipo === "apiladas") drawStacked(c, box);
    else drawHorizontal(c, box);
    fig.appendChild(box);

    var hasMarked = Object.keys(c.marked).length > 0;
    if (c.nota || hasMarked) {
      fig.appendChild(el("p", {
        class: "dchat-chart-note",
        text: (hasMarked ? "* " : "") + (c.nota || "Las barras marcadas tienen datos incompletos.")
      }));
    }

    // Tabla accesible con los mismos datos
    var headRow = el("tr", null, [el("th", { scope: "col", text: "Categoría" })]);
    c.series.forEach(function (s) { headRow.appendChild(el("th", { scope: "col", text: multi ? s.nombre : "Valor" })); });
    var tbody = el("tbody");
    c.labels.forEach(function (label, i) {
      var row = el("tr", null, [el("td", { text: label + (c.marked[i] ? " (datos incompletos)" : "") })]);
      c.series.forEach(function (s) { row.appendChild(el("td", { text: formatValue(valueAt(s, i), c.unit) })); });
      tbody.appendChild(row);
    });
    var table = el("table", null, [el("thead", null, [headRow]), tbody]);
    fig.appendChild(el("details", null, [el("summary", { text: "Ver los datos en tabla" }), table]));
    return fig;
  }

  /* Tabla de respaldo cuando no se pudo dibujar la gráfica.
     spec = { titulo, columnas: ["Enfermedad", "Consultas"], filas: [["Hipertensión", 151708], ...], nota } */
  function renderDataTable(spec) {
    var cols = Array.isArray(spec && spec.columnas) ? spec.columnas : [];
    var rows = Array.isArray(spec && spec.filas) ? spec.filas : [];
    if (!cols.length || !rows.length) return null;
    var fig = el("figure", { class: "dchat-chart dchat-table" });
    fig.appendChild(el("figcaption", { text: String(spec.titulo || "Datos") }));
    var head = el("tr");
    cols.forEach(function (c) { head.appendChild(el("th", { scope: "col", text: String(c) })); });
    var tbody = el("tbody");
    rows.forEach(function (r) {
      var tr = el("tr");
      (Array.isArray(r) ? r : []).forEach(function (v) {
        tr.appendChild(el("td", { text: typeof v === "number" ? formatNumber(v) : (v == null ? "—" : String(v)) }));
      });
      tbody.appendChild(tr);
    });
    fig.appendChild(el("table", null, [el("thead", null, [head]), tbody]));
    if (spec.nota) fig.appendChild(el("p", { class: "dchat-chart-note", text: String(spec.nota) }));
    return fig;
  }

  // Aclaraciones que agrega el backend (meses incompletos, consultas ≠ personas, privacidad…)
  function renderNotes(avisos) {
    var items = (Array.isArray(avisos) ? avisos : []).filter(function (a) { return a; });
    if (!items.length) return null;
    var list = el("ul");
    items.forEach(function (a) { list.appendChild(el("li", { text: String(a) })); });
    return el("details", { class: "dchat-notes", open: "" }, [
      el("summary", { text: "Ten en cuenta (" + items.length + ")" }),
      list
    ]);
  }

  // Líneas de apoyo que manda el backend: enlaces para llamar. El enlace solo lleva los dígitos del teléfono.
  function renderContacts(contactos) {
    var list = el("ul");
    (Array.isArray(contactos) ? contactos : []).forEach(function (c) {
      var digits = c && c.telefono ? String(c.telefono).replace(/[^0-9]/g, "") : "";
      if (!digits) return;
      var label = (c.nombre ? String(c.nombre) + ": " : "") + String(c.telefono) + (c.detalle ? " (" + String(c.detalle) + ")" : "");
      list.appendChild(el("li", null, [el("a", { href: "tel:" + digits, text: label })]));
    });
    if (!list.children.length) return null;
    return el("div", { class: "dchat-apoyo", role: "note" }, [el("p", { class: "dchat-apoyo-title", text: "Puedes llamar ahora:" }), list]);
  }

  function addBotReply(data) {
    var msg = el("div", { class: "dchat-msg dchat-msg--bot" + (data.tipo === "apoyo" ? " dchat-msg--apoyo" : "") });
    renderText(msg, data.respuesta || "No recibí una respuesta. Intenta con otra pregunta.");
    var contacts = renderContacts(data.contactos);
    if (contacts) msg.appendChild(contacts);
    if (data.grafica) {
      var chart = renderBarChart(data.grafica);
      if (chart) msg.appendChild(chart);
    } else if (data.tabla) {
      var dataTable = renderDataTable(data.tabla);
      if (dataTable) msg.appendChild(dataTable);
    }
    var notes = renderNotes(data.avisos);
    if (notes) msg.appendChild(notes);
    if (data.fuente) {
      msg.appendChild(el("div", { class: "dchat-source", text: "Fuente: " + data.fuente }));
    }
    messages.appendChild(msg);
    scrollDown();
  }

  function showTyping() {
    var wrap = el("div", { class: "dchat-msg dchat-msg--bot", "aria-label": "El asistente está escribiendo" }, [
      el("span", { class: "dchat-typing", "aria-hidden": "true" }, [el("span"), el("span"), el("span")]),
      el("span", { class: "dchat-sr-only", text: "El asistente está escribiendo" })
    ]);
    messages.appendChild(wrap);
    scrollDown();
    return wrap;
  }

  function setBusy(state) {
    busy = state;
    input.disabled = state;
    sendBtn.disabled = state;
  }

  // ---------- Comunicación con el backend ----------
  function send(rawText) {
    var text = String(rawText || "").trim();
    if (!text || busy) return;
    if (text.length > cfg.maxLength) text = text.slice(0, cfg.maxLength);

    open();
    suggestions.hidden = true;
    addMessage("user", text);
    input.value = "";
    setBusy(true);
    var typing = showTyping();

    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, cfg.timeoutMs);

    fetch(cfg.apiUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mensaje: text, sesion: sessionId }),
      signal: controller.signal
    })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (!res.ok) {
            var err = new Error("HTTP " + res.status);
            err.userMessage = data && data.respuesta; // el backend puede mandar un mensaje amable
            throw err;
          }
          return data;
        });
      })
      .then(function (data) {
        typing.remove();
        addBotReply(data);
      })
      .catch(function (err) {
        typing.remove();
        console.warn("[chat-widget] No se pudo completar la consulta:", err);
        addMessage("error", err.userMessage ||
          "No pude conectarme con el servidor de datos. Intenta de nuevo en un momento.");
      })
      .finally(function () {
        clearTimeout(timer);
        setBusy(false);
        input.focus();
      });
  }

  // ---------- Abrir y cerrar ----------
  function open() {
    if (panel.getAttribute("data-open") === "true") return;
    panel.setAttribute("data-open", "true");
    launcher.setAttribute("aria-expanded", "true");
    if (!started) {
      started = true;
      addMessage("bot", cfg.welcome);
    }
    setTimeout(function () { input.focus(); }, 50);
  }

  function close() {
    panel.setAttribute("data-open", "false");
    launcher.setAttribute("aria-expanded", "false");
    launcher.focus();
  }

  launcher.addEventListener("click", open);
  closeBtn.addEventListener("click", close);
  form.addEventListener("submit", function (e) { e.preventDefault(); send(input.value); });
  panel.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { e.stopPropagation(); close(); }
  });

  // ---------- Arranque ----------
  function mount() { document.body.appendChild(root); }
  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);

  window.DatosChat = { open: open, close: close, ask: send };
})();
