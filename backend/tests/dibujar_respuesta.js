/* Dibuja con el widget una respuesta real del backend (archivo JSON) y describe lo que quedó en pantalla.
   Uso: node dibujar_respuesta.js respuesta.json   → imprime un JSON con lo dibujado. */
"use strict";
const fs = require("fs");
const { crearEntorno } = require("./dom_simulado");

(async () => {
  const respuesta = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const entorno = crearEntorno();
  const m = await entorno.preguntar(respuesta);
  const grafica = m.buscar("dchat-chart").filter((f) => !f.className.includes("dchat-table"))[0];
  const tabla = m.buscar("dchat-table")[0];
  const etiquetas = grafica
    ? grafica.buscar("dchat-bar-label").concat(grafica.buscar("dchat-col-label")).map((e) => e.getAttribute("title"))
    : [];
  console.log(JSON.stringify({
    grafica_dibujada: Boolean(grafica),
    titulo: grafica ? grafica.porTag("figcaption")[0].textContent : null,
    barras: grafica ? grafica.buscar("dchat-bar-fill").length : 0,
    etiquetas,
    tabla_dibujada: Boolean(tabla),
    filas_tabla: tabla ? tabla.porTag("tbody")[0].children.length : 0,
    texto_visible: m.textContent,
  }));
})();
