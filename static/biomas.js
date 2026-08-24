/* Retoques al mapa de BlueMap — Server of Califree
 * (el fichero se llama biomas.js por historia; hoy hace cuatro cosas)
 *
 * Lo inyecta scripts/parche-bluemap.sh en /var/www/bluemap-web/index.html, como
 * script CLÁSICO en el <head>: así corre antes que el bundle de BlueMap (que es
 * un módulo y va diferido) y alcanza a engancharse a su evento de clic.
 *
 * Hace cuatro cosas:
 *   1. Sube el popup por encima de los marcadores. BlueMap dibuja marcadores y
 *      popup en la misma capa CSS2D y les pone el z-index según la distancia a
 *      la cámara, así que un icono cercano tapa la tarjeta. Un !important en
 *      hoja de estilos gana al z-index en línea que escribe su render.
 *   2. Añade el bioma (y los de debajo) a la tarjeta de clic, ordenado en
 *      filas: nombre a la izquierda, rango de altura a la derecha.
 *   3. Esconde los marcadores fuera de la vista plana cenital: en 3D y en vuelo
 *      libre tapaban el mundo entero.
 *   4. Entra en la VISTA PLANA por defecto (la segunda de las tres). BlueMap
 *      arranca en perspectiva salvo que la dirección traiga otra cosa.
 *
 * Para el bioma hay DOS caminos, por si BlueMap cambia de versión:
 *   a) el evento "bluemapMapInteraction", que trae las coordenadas exactas;
 *   b) si ese no llega, se leen las coordenadas del texto del propio popup.
 *
 * Diagnóstico: todo lo que hace se registra en la consola con el prefijo
 * [bioma]. Como el mapa va dentro de un <iframe> en el panel, esos mensajes se
 * mandan TAMBIÉN a la página de arriba con postMessage, así que se pueden leer
 * en la consola de https://califree.net/ sin abrir el mapa aparte.
 */
(function () {
  "use strict";

  var T = {
    es: { aqui: "Bioma", bajo: "Bajo tierra", cargando: "…", falla: "no pude leerlo" },
    en: { aqui: "Biome", bajo: "Underground", cargando: "…", falla: "couldn't read it" }
  };
  var peticion = 0;
  var ultimaClave = "";
  var yaPintado = 0;      // por si la respuesta llega antes que el "…" de espera

  function log() {
    var a = Array.prototype.slice.call(arguments);
    try {
      var b = a.slice();
      b.unshift("[bioma]");
      console.log.apply(console, b);
    } catch (_) {}
    // el mapa vive dentro de un iframe: que el panel de arriba también lo vea
    try {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage({ bioma: true, msg: a.map(function (x) {
          return (typeof x === "object" && x !== null) ? JSON.stringify(x) : String(x);
        }).join(" ") }, location.origin);
      }
    } catch (_) {}
  }

  // ---------------------------------------------------------------- 1) z-index
  function estilos() {
    var css = document.createElement("style");
    css.textContent =
      /* la tarjeta de clic y la de un marcador, por encima de todo */
      '[id^="bm-marker-"][class*="popup"],.bm-marker-popup,#bm-marker-popup{' +
      'z-index:2147483000 !important}' +
      '[id^="bm-marker-"][class*="popup"],.bm-marker-popup,#bm-marker-popup{' +
      'max-width:260px !important;width:auto !important}' +

      /* --- la parte del bioma dentro de la tarjeta ---------------------
         BlueMap pone .group>.content en display:flex EN FILA, así que una
         lista con varios renglones se le desarma: los nombres se partían en
         dos líneas y los "Y 115…24" quedaban flotando a la derecha. Aquí se
         vuelve columna y cada renglón se reparte nombre | altura. */
      '.bm-bioma{text-align:left}' +
      '.bm-bioma .sep{border:none;border-bottom:solid 1px var(--theme-bg-light,rgba(255,255,255,.18));' +
      'margin:.5em -.5em}' +
      '.bm-bioma .group{margin-top:.35em}' +
      '.bm-bioma .group>.label{position:relative;left:.5em;margin:0 .5em;font-size:.8em;' +
      'color:var(--theme-fg-light,#9aa4ae)}' +
      '#map-container .bm-marker-popup .bm-bioma .group>.content,' +
      '.bm-bioma .group>.content{display:flex !important;flex-direction:column !important;' +
      'align-items:stretch !important;justify-content:flex-start !important;gap:1px}' +
      '.bm-bioma .uno{text-align:center}' +
      '.bm-bioma .fila{display:flex;align-items:baseline;gap:.9em;justify-content:space-between;padding:0 .5em}' +
      /* el nombre puede partirse en dos renglones antes que salirle puntos
         suspensivos; la altura nunca se parte y se queda pegada a la derecha */
      '.bm-bioma .nom{min-width:0;white-space:normal;overflow-wrap:anywhere}' +
      '.bm-bioma .rango{flex:none;white-space:nowrap;font-size:.85em;opacity:.6;' +
      'font-variant-numeric:tabular-nums}' +
      '.bm-bioma .content,.bm-bioma .label,.bm-bioma .uno{overflow:visible !important;' +
      'text-overflow:clip !important;white-space:normal !important}' +

      /* marcadores escondidos salvo en la vista plana (la clase la pone
         vigilarVista); el popup y los jugadores nunca se esconden */
      'body.bm-solo-plano [id^="bm-marker-"]:not([class*="popup"])' +
      ':not([class*="player"]){display:none !important}';
    (document.head || document.documentElement).appendChild(css);
    log("estilos puestos (popup por encima de los marcadores)");
  }
  if (document.head) estilos();
  else document.addEventListener("DOMContentLoaded", estilos);

  // ------------------------------------------------- utilidades de BlueMap
  function app() {
    try { return window.bluemap || null; } catch (_) { return null; }
  }

  // el modo de cámara de verdad lo sabe la app; el hash solo se actualiza
  // 1,5 s DESPUÉS de mover la cámara, y en el panel arranca vacío
  function modoActual() {
    var a = app();
    try {
      if (a && a.appState && a.appState.controls && a.appState.controls.state)
        return String(a.appState.controls.state).toLowerCase();
    } catch (_) {}
    return ((location.hash || "").split(":").pop() || "").toLowerCase();
  }

  // ------------------------------ 4) vista plana por defecto (la segunda)
  // El hash manda: si la dirección ya trae ":flat" / ":free" / ":perspective"
  // se respeta. Dentro del panel el iframe abre /map/ sin hash, y ahí BlueMap
  // entraría en perspectiva — que es justo lo que no queremos.
  var HASH_INICIAL = (location.hash || "");
  var TRAE_VISTA = /:(flat|free|perspective)\s*$/i.test(HASH_INICIAL);

  // Hasta que la vista de arranque esté puesta, NO se esconde ningún marcador:
  // BlueMap nace en "perspective" y si el vigilante actuara en ese instante
  // los iconos se irían y volverían — o se quedarían fuera si algo fallara.
  // Lo que Juan quiere ver al entrar es el mapa plano CON los iconos.
  var vistaFijada = TRAE_VISTA;

  function vistaPlanaPorDefecto() {
    if (TRAE_VISTA) { log("la dirección ya trae vista, no la toco"); return; }
    var intentos = 0;
    // cada 100 ms, no cada 500: cuanto antes entre en plano, menos parpadeo
    var t = setInterval(function () {
      var a = app();
      intentos++;
      if (a && typeof a.setFlatView === "function" && a.mapViewer && a.mapViewer.map) {
        clearInterval(t);
        try {
          a.setFlatView(0);
          log("vista plana puesta por defecto (" + (intentos * 100) + " ms)");
        } catch (e) { log("no pude poner la vista plana:", e); }
        vistaFijada = true;
        setTimeout(vigilarVista, 50);
      } else if (intentos > 300) {         // 30 s y me rindo
        clearInterval(t);
        vistaFijada = true;
        log("BlueMap no apareció; me quedo con su vista por defecto");
      }
    }, 100);
  }
  vistaPlanaPorDefecto();

  // ------------------------------------------- 3) iconos solo en vista plana
  // Si el modo no se reconoce, NO se esconde nada (mejor de más que de menos).
  var vistaAnterior = null;

  function vigilarVista() {
    if (!vistaFijada) return;              // aún colocando la vista de arranque
    var modo = modoActual();
    var ocultar = (modo === "perspective" || modo === "free");
    if (ocultar === vistaAnterior) return;
    vistaAnterior = ocultar;
    if (document.body) document.body.classList.toggle("bm-solo-plano", ocultar);
    log("vista '" + modo + "' →", ocultar ? "marcadores ocultos" : "marcadores visibles");
  }
  addEventListener("hashchange", vigilarVista);
  setInterval(vigilarVista, 700);          // los botones de vista no tocan el hash al instante
  if (document.body) vigilarVista();
  else document.addEventListener("DOMContentLoaded", vigilarVista);

  // ---------------------------------------------------------------- utilidades
  function idioma() {
    try { return localStorage.getItem("panel_lang") === "en" ? "en" : "es"; }
    catch (_) { return "es"; }
  }

  // dentro del panel el iframe abre /map/ sin hash, así que hay que
  // preguntarle a BlueMap qué mapa tiene puesto (overworld / nether / end)
  function mapaActual() {
    var h = (location.hash || "").replace(/^#/, "").split(":")[0];
    if (h) return h;
    var a = app();
    try {
      if (a && a.mapViewer && a.mapViewer.map && a.mapViewer.map.data)
        return a.mapViewer.map.data.id || "";
    } catch (_) {}
    return "";
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
    caja.innerHTML = '<hr class="sep">' + html;
  }

  function quitar() {
    var el = popup();
    if (!el) return;
    var caja = el.querySelector(".bm-bioma");
    if (caja) caja.remove();
  }

  function render(d) {
    var l = T[idioma()], en = idioma() === "en";
    var html = grupo(l.aqui, '<div class="uno">' + esc((en ? d.en : d.es) || d.id) + "</div>");
    if (d.debajo && d.debajo.length) {
      html += grupo(l.bajo, d.debajo.map(function (b) {
        return '<div class="fila"><span class="nom">' +
               esc((en ? b.en : b.es) || b.id) +
               '</span><span class="rango">Y ' + b.y1 + "…" + b.y0 + "</span></div>";
      }).join(""));
    }
    pinta(html);
  }

  // y === null  ->  "auto": que el servidor use la cima de la columna
  function consultar(x, y, z, origen) {
    var clave = x + "/" + y + "/" + z;
    if (clave === ultimaClave) return;      // el mismo punto otra vez, no repetir
    ultimaClave = clave;
    var mio = ++peticion;
    var mapa = mapaActual();
    log("consultando", clave, "en '" + (mapa || "(sin mapa)") + "' (" + origen + ")");
    setTimeout(function () {
      if (mio === peticion && yaPintado !== mio)
        pinta(grupo(T[idioma()].aqui, '<div class="uno">' + T[idioma()].cargando + "</div>"));
    }, 0);

    var url = "/api/biome?mapa=" + encodeURIComponent(mapa) +
              "&x=" + x + "&y=" + (y === null ? "auto" : y) + "&z=" + z;
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        log("respuesta", r.status, url);
        if (!r.ok) {
          if (mio === peticion) {
            yaPintado = mio;
            pinta(grupo(T[idioma()].aqui,
              '<div class="uno">' + T[idioma()].falla + " (" + r.status + ")</div>"));
          }
          return null;
        }
        return r.json();
      })
      .then(function (d) {
        if (mio !== peticion || d === null) return;
        if (!d || !d.found) { log("sin bioma ahí", d); yaPintado = mio; quitar(); return; }
        log("bioma:", d.es, d.debajo ? "(" + d.debajo.length + " debajo)" : "");
        yaPintado = mio;
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
        log("enganchado a bluemapMapInteraction");
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

  log("cargado" + (TRAE_VISTA ? " (con vista en la dirección)" : " (sin vista en la dirección)"));
})();
