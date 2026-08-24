/* Bioma en el popup de BlueMap — Server of Califree
 *
 * BlueMap no dice en qué bioma hiciste clic. Este script se cuela en el webapp
 * de BlueMap (lo inyecta scripts/parche-bluemap.sh en /var/www/bluemap-web/index.html),
 * escucha el mismo evento que usa el popup nativo y le añade una línea más.
 *
 * Truco para no depender de variables globales de BlueMap: se parchea
 * addEventListener para quedarse con el MISMO objeto sobre el que BlueMap
 * registra "bluemapMapInteraction". Por eso este script tiene que cargarse
 * ANTES del bundle de BlueMap (script clásico en el <head>; los módulos van
 * diferidos y se ejecutan después).
 */
(function () {
  "use strict";

  var T = {
    es: { aqui: "Bioma", bajo: "Bajo tierra", cargando: "…" },
    en: { aqui: "Biome", bajo: "Underground", cargando: "…" }
  };
  var peticion = 0;

  function idioma() {
    try { return localStorage.getItem("panel_lang") === "en" ? "en" : "es"; }
    catch (_) { return "es"; }
  }

  function mapaActual() {
    // la url del webapp es  #<mapa>:x:y:z:...
    var h = (location.hash || "").replace(/^#/, "");
    return h.split(":")[0] || "";
  }

  function popup() {
    return document.getElementById("bm-marker-popup");
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function grupo(etiqueta, contenidoHtml) {
    return '<div class="group"><div class="label">' + esc(etiqueta) +
           '</div><div class="content">' + contenidoHtml + "</div></div>";
  }

  function pinta(html) {
    var el = popup();
    if (!el) return;
    var caja = el.querySelector(".bm-bioma");
    if (!caja) {
      caja = document.createElement("div");
      caja.className = "bm-bioma";
      el.appendChild(caja);
    }
    caja.innerHTML = html;
  }

  function quitar() {
    var el = popup();
    if (!el) return;
    var caja = el.querySelector(".bm-bioma");
    if (caja) caja.remove();
  }

  function render(d) {
    var l = T[idioma()], en = idioma() === "en";
    var html = grupo(l.aqui, esc((en ? d.en : d.es) || d.id));
    if (d.debajo && d.debajo.length) {
      var filas = d.debajo.map(function (b) {
        return esc((en ? b.en : b.es) || b.id) +
               ' <span style="opacity:.55;white-space:nowrap">Y ' + b.y1 + "…" + b.y0 + "</span>";
      }).join("<br>");
      html += grupo(l.bajo, filas);
    }
    pinta(html);
  }

  function consultar(x, y, z) {
    var mio = ++peticion;
    // el popup se construye en el mismo turno de eventos: esperamos a que exista
    setTimeout(function () {
      if (mio !== peticion) return;
      pinta(grupo(T[idioma()].aqui, T[idioma()].cargando));
    }, 0);

    var url = "/api/biome?mapa=" + encodeURIComponent(mapaActual()) +
              "&x=" + x + "&y=" + y + "&z=" + z;
    fetch(url, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (mio !== peticion) return;
        if (!d || !d.found) { quitar(); return; }
        render(d);
      })
      .catch(function () { if (mio === peticion) quitar(); });
  }

  function alInteractuar(evt) {
    var d = (evt && evt.detail) || {};
    var golpe = d.hiresHit || (d.lowresHits && d.lowresHits[0]);
    var p = golpe && golpe.point;
    if (!p) { peticion++; return; }
    consultar(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z));
  }

  var original = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function (tipo, fn, opciones) {
    if (tipo === "bluemapMapInteraction" && !this.__biomaEnganchado) {
      this.__biomaEnganchado = true;
      try { original.call(this, tipo, alInteractuar, false); } catch (_) {}
    }
    return original.call(this, tipo, fn, opciones);
  };
})();
