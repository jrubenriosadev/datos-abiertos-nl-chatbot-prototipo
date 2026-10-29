/* Prueba del widget con un DOM mínimo simulado (sin dependencias).
   La ejecuta test_widget.py con Node. Sale con código 1 si alguna verificación falla. */
"use strict";
const { crearEntorno } = require("./dom_simulado");

const entorno = crearEntorno();
const { todos, raiz, widget } = entorno;
const preguntar = (respuesta, estado) => entorno.preguntar(respuesta, estado);

let fallas = 0;
function verificar(condicion, mensaje) {
  if (!condicion) { fallas++; console.log("FALLA: " + mensaje); }
}

(async () => {
  // 1. Barras verticales con meses marcados, avisos y fuente.
  let m = await preguntar({
    respuesta: "Primer párrafo.\n\n- uno\n- dos",
    grafica: { titulo: "Consultas por mes", tipo: "barras", unidad: "consultas",
               etiquetas: ["Mayo de 2026", "Junio de 2026", "Julio de 2026", "Agosto de 2026", "Septiembre de 2026"],
               valores: [89662, 94869, 91081, 75616, 1923], marcadas: [0, 3, 4], nota: "Meses incompletos." },
    fuente: "Consulta General y Especialidades, periodo de prueba",
    avisos: ["Septiembre está incompleto.", "Cada registro es una consulta."],
  });
  verificar(m.className.includes("dchat-msg--bot"), "la respuesta es un mensaje del bot");
  verificar(m.porTag("ul")[0].children.length === 2, "la lista '- ' se dibuja como <ul>");
  verificar(m.buscar("dchat-col").length === 5, "5 columnas verticales");
  verificar(m.buscar("dchat-bar--marcada").length === 3, "3 barras marcadas");
  verificar(m.buscar("dchat-col-label")[0].textContent === "Mayo de 2026 *", "etiqueta marcada con asterisco");
  verificar(m.buscar("dchat-chart-note")[0].textContent === "* Meses incompletos.", "nota de la gráfica");
  const alturas = m.buscar("dchat-bar-fill").map((f) => parseFloat(f.style.height));
  verificar(alturas[1] === 100 && Math.abs(alturas[4] - 100 * 1923 / 94869) < 0.01, "alturas proporcionales al máximo");
  const celdas = m.porTag("td").map((t) => t.textContent);
  verificar(celdas.includes("94,869 consultas") && celdas.includes("Mayo de 2026 (datos incompletos)"), "tabla accesible con valores y marcas");
  verificar(m.buscar("dchat-notes")[0].porTag("summary")[0].textContent === "Ten en cuenta (2)", "recuadro de avisos");
  verificar(m.buscar("dchat-notes")[0].getAttribute("open") !== null, "avisos abiertos por defecto");
  verificar(m.buscar("dchat-source")[0].textContent.startsWith("Fuente: Consulta General"), "fuente");

  // 2. Más de 8 categorías en 'barras' → horizontales.
  m = await preguntar({ respuesta: "x", grafica: { titulo: "t", tipo: "barras", etiquetas: "abcdefghij".split(""), valores: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] } });
  verificar(m.buscar("dchat-col").length === 0 && m.buscar("dchat-bar-row").length === 10, "más de 8 barras se dibujan horizontales");

  // 3. Agrupadas con valor nulo y porcentajes.
  m = await preguntar({ respuesta: "x", grafica: { titulo: "Por sexo", tipo: "agrupadas", unidad: "%", etiquetas: ["A", "B"],
    series: [{ nombre: "Femenino", valores: [60.5, null] }, { nombre: "Masculino", valores: [39.5, 40] }] } });
  verificar(m.buscar("dchat-bar-group").length === 2 && m.buscar("dchat-bar-sub").length === 4, "agrupadas: 2 grupos × 2 series");
  verificar(m.buscar("dchat-legend")[0].textContent === "FemeninoMasculino", "leyenda con las series");
  const valores = m.buscar("dchat-bar-value").map((v) => v.textContent);
  verificar(valores.includes("60.5%") && valores.includes("—"), "porcentaje y valor nulo como —");
  verificar(m.porTag("th").map((t) => t.textContent).join("|") === "Categoría|Femenino|Masculino", "tabla con una columna por serie");

  // 4. Apiladas: total por categoría.
  m = await preguntar({ respuesta: "x", grafica: { titulo: "t", tipo: "apiladas", unidad: "consultas", etiquetas: ["A"],
    series: [{ nombre: "s1", valores: [30] }, { nombre: "s2", valores: [70] }] } });
  const seg = m.buscar("dchat-bar-fill").map((f) => parseFloat(f.style.width));
  verificar(seg[0] === 30 && seg[1] === 70, "apiladas: segmentos proporcionales al total");
  verificar(m.buscar("dchat-bar-value")[0].textContent === "100 consultas", "apiladas: total");

  // 5. Contrato anterior (sin tipo ni avisos) sigue funcionando.
  m = await preguntar({ respuesta: "x", grafica: { titulo: "t", etiquetas: ["A", "B"], valores: [1, 2] }, fuente: null });
  verificar(m.buscar("dchat-bar-row").length === 2 && m.buscar("dchat-notes").length === 0, "contrato anterior compatible");

  // 6. El texto del servidor nunca se interpreta como HTML.
  m = await preguntar({ respuesta: "<img src=x onerror=alert(1)>", grafica: { titulo: "<b>t</b>", etiquetas: ["<i>A</i>"], valores: [1] },
    avisos: ["<script>alert(1)</script>"], fuente: "<a href=x>f</a>" });
  verificar(m.textContent.includes("<img src=x onerror=alert(1)>"), "el texto con etiquetas se muestra literal");
  const conHtml = todos.filter((n) => n._innerHTML !== null);
  verificar(conHtml.length > 0 && conHtml.every((n) => n._innerHTML.startsWith("<svg")), "innerHTML solo con íconos fijos");
  verificar(todos.every((n) => n.tagName !== "SCRIPT" && n.tagName !== "IMG"), "no se crean <script> ni <img> desde datos");

  // 7. Errores del backend: se muestra su mensaje.
  m = await preguntar({ respuesta: "Espera un minuto e inténtalo de nuevo." }, 429);
  verificar(m.className.includes("dchat-msg--error") && m.textContent === "Espera un minuto e inténtalo de nuevo.", "mensaje de error del backend");

  // 8. Ningún marcador interno llega a la pantalla.
  m = await preguntar({ respuesta: "Estas son las más frecuentes:\n\n[[GRAFICA_AQUI]]\n\nY {{grafica}} fin.",
    grafica: { titulo: "t", etiquetas: ["Hipertensión esencial primaria"], valores: [5] } });
  verificar(!m.textContent.includes("[[") && !m.textContent.includes("{{"), "los marcadores se quitan del texto");
  verificar(m.textContent.includes("Estas son las más frecuentes:") && m.textContent.includes("Y fin."), "el resto del texto se conserva");

  // 9. Sin gráfica: tabla de respaldo con los datos.
  m = await preguntar({ respuesta: "No se pudo dibujar la gráfica; abajo tienes los mismos datos en una tabla.", grafica: null,
    tabla: { titulo: "Datos de la consulta", columnas: ["Enfermedad", "Consultas"], filas: [["Hipertensión", 151708], ["Resfriado", 149948]] } });
  verificar(m.buscar("dchat-table").length === 1, "se dibuja la tabla de respaldo");
  verificar(m.porTag("td").map((x) => x.textContent).join("|") === "Hipertensión|151,708|Resfriado|149,948", "tabla con nombres y cifras");

  // 10. Botón para ampliar y reducir la ventana.
  const panel = raiz.buscar("dchat-panel")[0];
  const boton = raiz.buscar("dchat-size")[0];
  verificar(panel.getAttribute("data-size") === "normal" && boton.getAttribute("aria-pressed") === "false", "empieza en tamaño normal");
  boton.click();
  verificar(panel.getAttribute("data-size") === "grande" && boton.getAttribute("aria-pressed") === "true"
            && boton.getAttribute("aria-label") === "Reducir la ventana", "se amplía y cambia su etiqueta");
  boton.click();
  verificar(panel.getAttribute("data-size") === "normal" && boton.getAttribute("aria-label") === "Ampliar la ventana", "se reduce");

  // 11. Etiquetas largas completas (sin recortar): el texto entero queda en la etiqueta.
  const larga = "Diabetes mellitus no especificada, sin mención de complicación";
  m = await preguntar({ respuesta: "x", grafica: { titulo: "t", tipo: "horizontales", etiquetas: [larga], valores: [3] } });
  verificar(m.buscar("dchat-bar-label")[0].textContent === larga, "la etiqueta larga se muestra completa");

  // 12. Mensaje de apoyo: estilo propio y teléfonos como enlaces tel: con solo dígitos.
  m = await preguntar({ respuesta: "Siento mucho que estés pasando por esto.", tipo: "apoyo",
    contactos: [{ nombre: "Línea de prueba", telefono: "800 123 4567", detalle: "24 horas" },
                { nombre: "Emergencias", telefono: "911" }, { nombre: "x", telefono: "javascript:alert(1)" }] });
  verificar(m.className.includes("dchat-msg--apoyo"), "el mensaje de apoyo tiene su estilo");
  const enlaces = m.porTag("a");
  verificar(enlaces.length === 3 && enlaces[0].getAttribute("href") === "tel:8001234567", "teléfono como enlace tel:");
  verificar(enlaces[0].textContent === "Línea de prueba: 800 123 4567 (24 horas)", "etiqueta del contacto");
  verificar(enlaces.every((a) => /^tel:[0-9]+$/.test(a.getAttribute("href"))), "los enlaces solo llevan dígitos");
  m = await preguntar({ respuesta: "Respuesta normal." });
  verificar(!m.className.includes("dchat-msg--apoyo") && m.porTag("a").length === 0, "sin contactos no hay enlaces");

  console.log(fallas ? `${fallas} verificaciones fallaron` : "OK: todas las verificaciones del widget pasaron");
  process.exit(fallas ? 1 : 0);
})();
