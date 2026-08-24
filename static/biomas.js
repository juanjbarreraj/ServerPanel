/* Retoques al mapa de BlueMap — Server of Califree
 * (el fichero se llama biomas.js por historia; hoy hace tres cosas)
 *
 * Lo inyecta scripts/parche-bluemap.sh en /var/www/bluemap-web/index.html, como
 * script CLÁSICO en el <head>: así corre antes que el bundle de BlueMap (que es
 * un módulo y va diferido) y alcanza a engancharse a su evento de clic.
 *
 * Hace tres cosas:
 *   1. Sube el popup por encima de los marcadores. BlueMap dibuja marcadores y
 *      popup en la misma capa CSS2D y les pone el z-index según la distancia a
 *      la cámara, así que un icono cercano tapa la tarjeta. Un !important en
 *      hoja de estilos gana al z-index en línea que escribe su render.
 *   2. Añade el bioma (y los de debajo) a la tarjeta de clic.
 *   3. Esconde los marcadores fuera de la vista plana cenital: en 3D y en vuelo
 *      libre tapaban el mundo entero.
 *
 * Para el bioma hay DOS caminos, por si BlueMap cambia de versión:
 *   a) el evento "bluemapMapInteraction", que trae las coordenadas exactas;
 *   b) si ese no llega, se leen las coordenadas del texto del propio popup.
 *
 * Diagnóstico: todo lo que hace se registra en la consola con el prefijo
 * [bioma]. Si algo no funciona, abrir la consola del navegador (F12) sobre el
 * mapa y mirar esas líneas.
 */
(function () {
  "use strict";

  var T = {
    es: { aqui: "Bioma", bajo: "Bajo tierra", cargando: "…" },
    en: { aqui: "Biome", bajo: "Underground", cargando: "…" }
  };
  var peticion = 0;
  var ultimaClave = "";

  function log() {
    try {
      var a = Array.prototype.slice.call(arguments);
      a.unshift("[bioma]");
      console.log.apply(console, a);
    } catch (_) {}
  }

  // ---------------------------------------------------------------- 1) z-index
  function estilos() {
    var css = document.createElement("style");
    css.textContent =
      /* la tarjeta de clic y la de un marcador, por encima de todo */
      '[id^="bm-marker-"][class*="popup"],.bm-marker-popup,#bm-marker-popup{' +
      'z-index:2147483000 !important}' +
      /* el contenido del bioma hereda el estilo del popup, pero SIN recortar:
         BlueMap le pone text-overflow:ellipsis y "Llanura de girasoles" se
         quedaba en "Llanura de gira…" */
      '.bm-bioma .group{margin-top:4px}' +
      '.bm-bioma .content,.bm-bioma .label{white-space:normal !important;' +
      'overflow:visible !important;text-overflow:clip !important}' +
      '[id^="bm-marker-"][class*="popup"],.bm-marker-popup,#bm-marker-popup{' +
      'max-width:260px !important;width:auto !important}' +
      /* marcadores escondidos salvo en la vista plana (la clase la pone
         vigilarVista); el popup y los jugadores nunca se esconden */
      'body.bm-solo-plano [id^="bm-marker-"]:not([class*="popup"])' +
      ':not([class*="player"]){display:none !important}';
    (document.head || document.documentElement).appendChild(css);
    log("estilos puestos (popup por encima de los marcadores)");
  }
  if (document.head) estilos();
  else document.addEventListener("DOMContentLoaded", estilos);

  // ------------------------------------------- 3) iconos solo en vista plana
  // El modo de cámara va en el último trozo del hash: perspective | flat | free.
  // Si no se reconoce, NO se esconde nada (mejor de más que de menos).
  var vistaAnterior = null;

  function vigilarVista() {
    var modo = ((location.hash || "").split(":").pop() || "").toLowerCase();
    var ocultar = (modo === "perspective" || modo === "free");
    if (ocultar === vistaAnterior) return;
    vistaAnterior = ocultar;
    if (document.body) document.body.classList.toggle("bm-solo-plano", ocultar);
    log("vista '" + modo + "' →", ocultar ? "marcadores ocultos" : "marcadores visibles");
  }
  addEventListener("hashchange", vigilarVista);
  if (document.body) vigilarVista();
  else document.addEventListener("DOMContentLoaded", vigilarVista);

  // ---------------------------------------------------------------- utilidades
  function idioma() {
    try { return localStorage.getItem("panel_lang") === "en" ? "en" : "es"; }
    catch (_) { return "es"; }
  }

  function mapaActual() {
    var h = (location.hash || "").replace(/^#/, "");
    return h.split(":")[0] || "";
  }

  function popup() {
    return document.getElementById("bm-marker-popup") ||
           document.querySelector('[id^="bm-marker-"][class*="popup"]') ||
           document.querySelector(".bm-marker-popup");
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
    if (!el) { log("no encuentro el popup para pintar"); return; }
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
      html += grupo(l.bajo, d.debajo.map(function (b) {
        return esc((en ? b.en : b.es) || b.id) +
               ' <span style="opacity:.55;white-space:nowrap">Y ' + b.y1 + "…" + b.y0 + "</span>";
      }).join("<br>"));
    }
    pinta(html);
  }

  // y === null  ->  "auto": que el servidor use la cima de la columna
  function consultar(x, y, z, origen) {
    var clave = x + "/" + y + "/" + z;
    if (clave === ultimaClave) return;      // el mismo punto otra vez, no repetir
    ultimaClave = clave;
    var mio = ++peticion;
    log("consultando", clave, "(" + origen + ")");
    setTimeout(function () {
      if (mio === peticion) pinta(grupo(T[idioma()].aqui, T[idioma()].cargando));
    }, 0);

    var url = "/api/biome?mapa=" + encodeURIComponent(mapaActual()) +
              "&x=" + x + "&y=" + (y === null ? "auto" : y) + "&z=" + z;
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        log("respuesta", r.status, url);
        return r.ok ? r.json() : null;
      })
      .then(function (d) {
        if (mio !== peticion) return;
        if (!d || !d.found) { log("sin bioma ahí", d); quitar(); return; }
        log("bioma:", d.es, d.debajo ? "(" + d.debajo.length + " debajo)" : "");
        render(d);
      })
      .catch(function (e) { log("falló la petición:", e); if (mio === peticion) quitar(); });
  }

  // ------------------------------------------------- 2a) el evento de BlueMap
  function alInteractuar(evt) {
    var d = (evt && evt.detail) || {};
    var hires = d.hiresHit;
    var golpe = hires || (d.lowresHits && d.lowresHits[0]);
    var p = golpe && golpe.point;
    if (!p) { peticion++; ultimaClave = ""; return; }
    // solo el golpe de alta resolución trae una altura real; en el mapa plano
    // BlueMap devuelve y=0 y eso es subsuelo, así que se pide la superficie
    consultar(Math.floor(p.x), hires ? Math.floor(p.y) : null, Math.floor(p.z),
              hires ? "evento" : "evento (plano → superficie)");
  }

  var original = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function (tipo, fn, opciones) {
    if (tipo === "bluemapMapInteraction" && !this.__biomaEnganchado) {
      this.__biomaEnganchado = true;
      try {
        original.call(this, tipo, alInteractuar, false);
        log("enganchado a bluemapMapInteraction en", this);
      } catch (e) { log("no pude engancharme:", e); }
    }
    return original.call(this, tipo, fn, opciones);
  };

  // --------------------------------------- 2b) respaldo: leer el propio popup
  // Si BlueMap cambiara el nombre del evento, esto sigue funcionando: mira
  // cuándo aparece la tarjeta y saca las coordenadas de su texto.
  // acepta el guion normal y el signo menos tipográfico (−), que es el que sale
  // en las tarjetas de estructura por la fuente monoespaciada
  var COORD = /x[:\s]*([-\u2212]?\d+)[,\s]+y[:\s]*([-\u2212]?\d+)[,\s]+z[:\s]*([-\u2212]?\d+)/i;
  function num(s) { return parseInt(String(s).replace(/\u2212/g, "-"), 10); }

  function revisarPopup() {
    var el = popup();
    if (!el) { ultimaClave = ""; return; }
    if (el.querySelector(".bm-bioma")) return;         // ya lo tiene
    var m = COORD.exec(el.innerText || "");
    if (!m) return;
    consultar(num(m[1]), num(m[2]), num(m[3]), "texto del popup");
  }

  function observar() {
    try {
      new MutationObserver(function () { revisarPopup(); })
        .observe(document.body, { childList: true, subtree: true, characterData: true });
      log("observador del popup activo (respaldo)");
    } catch (e) { log("sin observador:", e); }
  }
  if (document.body) observar();
  else document.addEventListener("DOMContentLoaded", observar);

  log("cargado");
})();
