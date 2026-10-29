/* DOM mínimo simulado para probar el widget con Node, sin dependencias.
   Carga frontend/chat-widget.js en un contexto aislado y permite simular respuestas del backend. */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

function crearEntorno() {
  const todos = [];
  class Nodo {
    constructor(tag) {
      this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {}; this.style = {};
      this.className = ""; this._text = ""; this._innerHTML = null; this.parentNode = null; this.hidden = false;
      this.listeners = {};
      todos.push(this);
    }
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
    remove() { if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); }
    setAttribute(k, v) { this.attributes[k] = String(v); }
    getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; }
    addEventListener(tipo, fn) { (this.listeners[tipo] = this.listeners[tipo] || []).push(fn); }
    click() { (this.listeners.click || []).forEach((fn) => fn({ preventDefault() {} })); }
    focus() {}
    get scrollHeight() { return 0; }
    set textContent(v) { this._text = String(v); this.children = []; }
    get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
    set innerHTML(v) { this._innerHTML = String(v); }
    get innerHTML() { return this._innerHTML || ""; }
    buscar(clase) {
      const r = [];
      const recorrer = (n) => n.children.forEach((c) => {
        if ((" " + c.className + " ").includes(" " + clase + " ")) r.push(c);
        recorrer(c);
      });
      recorrer(this);
      return r;
    }
    porTag(tag) {
      const r = [];
      const recorrer = (n) => n.children.forEach((c) => { if (c.tagName === tag.toUpperCase()) r.push(c); recorrer(c); });
      recorrer(this);
      return r;
    }
  }

  const estado = { respuesta: null, codigo: 200 };
  const contexto = {
    window: { DatosChatConfig: { apiUrl: "/api/chat" } },
    document: { createElement: (t) => new Nodo(t), body: new Nodo("body"), addEventListener() {} },
    fetch: async () => ({ ok: estado.codigo === 200, status: estado.codigo, json: async () => estado.respuesta }),
    crypto: { randomUUID: () => "uuid-de-prueba-0001" },
    setTimeout, clearTimeout, AbortController, console: { warn() {}, log: console.log },
  };
  contexto.window.crypto = contexto.crypto;
  vm.createContext(contexto);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "..", "frontend", "chat-widget.js"), "utf8"), contexto);

  const raiz = contexto.document.body;
  return {
    todos, raiz, widget: contexto.window.DatosChat,
    /** Simula una respuesta del backend y devuelve el último mensaje dibujado. */
    async preguntar(respuesta, codigo = 200) {
      estado.respuesta = respuesta; estado.codigo = codigo;
      contexto.window.DatosChat.ask("pregunta de prueba");
      await new Promise((r) => setTimeout(r, 20));
      const msgs = raiz.buscar("dchat-msg");
      return msgs[msgs.length - 1];
    },
  };
}

module.exports = { crearEntorno };
