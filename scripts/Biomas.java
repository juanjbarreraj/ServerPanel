package califree;

// Le pregunta los biomas al generador de mundos DE MINECRAFT, el que viene
// dentro de tu server.jar, y los sirve por HTTP para que el panel dibuje el
// mapa estilo Chunkbase.
//
// POR QUÉ SE PUEDE HACER ASÍ
// El jar de la 26.2 viene SIN OFUSCAR (7.434 de 7.445 clases con su nombre
// real), y por eso Mojang ha dejado de publicar las tablas de equivalencias: ya
// no hacen falta. Esto se compila contra el jar como contra cualquier librería.
//
// NO GENERA CHUNKS NI ESCRIBE NADA EN EL MUNDO. Solo hace la cuenta de qué
// bioma toca en cada sitio — lo mismo que Chunkbase, pero con el generador de
// verdad en vez de con una reimplementación, así que no puede desviarse ni
// quedarse atrás cuando salga una versión nueva.
//
// POR QUÉ ES UN SERVICIO Y NO UN COMANDO
// Arrancar Minecraft tarda ~6,6 segundos y calcular un azulejo entero, uno.
// Un proceso por petición sería insufrible; este se queda vivo y responde en
// milisegundos.
//
// POR QUÉ DEVUELVE NÚMEROS Y NO UNA IMAGEN
// Los colores los pone el panel, no esto. Cambiar un color no puede obligar a
// recompilar Java: en el servidor de Juan no hay compilador, así que cada
// recompilación es un viaje de ida y vuelta. Devolviendo el id del bioma por
// punto, la paleta se retoca en Python y se ve al recargar.

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import net.minecraft.SharedConstants;
import net.minecraft.core.Holder;
import net.minecraft.core.HolderGetter;
import net.minecraft.core.HolderLookup;
import net.minecraft.core.registries.Registries;
import net.minecraft.data.registries.VanillaRegistries;
import net.minecraft.server.Bootstrap;
import net.minecraft.world.level.biome.Climate;
import net.minecraft.world.level.biome.MultiNoiseBiomeSource;
import net.minecraft.world.level.biome.MultiNoiseBiomeSourceParameterLists;
import net.minecraft.world.level.levelgen.NoiseGeneratorSettings;
import net.minecraft.world.level.levelgen.RandomState;

import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

public class Biomas {

    // A qué altura se pregunta. Los biomas en Minecraft son tridimensionales:
    // a y=64 buena parte del mundo devuelve biomas de CUEVA (dripstone_caves,
    // lush_caves…) porque ahí abajo hay cuevas, y el mapa saldría moteado de
    // gris. Medido en el mundo de Juan: a y=64 su base daba dripstone_caves y a
    // partir de y≈120 sale grove, que es el de superficie. 128 va sobrado.
    static final int Y_SUPERFICIE = 128;

    private final MultiNoiseBiomeSource fuente;
    private final Climate.Sampler muestreador;

    // La leyenda es FIJA, no se va llenando sobre la marcha.
    //
    // Antes numeraba los biomas según iban apareciendo. Eso rompe el caché: un
    // azulejo guardado hoy dice «3» queriendo decir río, y mañana, tras
    // reiniciar el servicio y mirar primero otra zona, «3» es otra cosa y el
    // mapa sale con los colores cambiados. Numerando TODOS los biomas del
    // registro en orden alfabético al arrancar, el 3 es siempre el mismo bioma
    // aunque se reinicie, y los azulejos de disco siguen valiendo.
    private static final List<String> NOMBRES = new ArrayList<>();
    private static final Map<String, Integer> ID_POR_NOMBRE = new HashMap<>();

    public Biomas(HolderLookup.Provider registros, long semilla) {
        HolderGetter<net.minecraft.world.level.biome.MultiNoiseBiomeSourceParameterList> presets =
                registros.lookupOrThrow(Registries.MULTI_NOISE_BIOME_SOURCE_PARAMETER_LIST);
        this.fuente = MultiNoiseBiomeSource.createFromPreset(
                presets.getOrThrow(MultiNoiseBiomeSourceParameterLists.OVERWORLD));

        // Aquí entra la semilla: RandomState es lo que la convierte en los
        // ruidos concretos de este mundo.
        RandomState estado = RandomState.create(
                registros, NoiseGeneratorSettings.OVERWORLD, semilla);
        this.muestreador = estado.sampler();
    }

    static synchronized void numerarBiomas(HolderLookup.Provider registros) {
        if (!NOMBRES.isEmpty()) return;
        List<String> todos = new ArrayList<>();
        registros.lookupOrThrow(Registries.BIOME).listElementIds()
                // en la 26.x ResourceLocation se llama Identifier y el método
                // location() pasó a llamarse identifier()
                .forEach(k -> todos.add(k.identifier().toString()));
        todos.sort(String::compareTo);
        for (String n : todos) {
            ID_POR_NOMBRE.put(n, NOMBRES.size());
            NOMBRES.add(n);
        }
    }

    private static int indice(String nombre) {
        Integer i = ID_POR_NOMBRE.get(nombre);
        return i == null ? 255 : i;      // 255 = desconocido, el panel lo pinta chillón
    }

    public String nombreEn(int x, int y, int z) {
        // getNoiseBiome trabaja en CUARTOS de bloque: el mundo guarda un bioma
        // por celda de 4x4x4. Pasarle coordenadas de bloque sin dividir daría un
        // mapa cuatro veces más grande — se vería plausible y estaría mal.
        Holder<?> h = fuente.getNoiseBiome(x >> 2, y >> 2, z >> 2, muestreador);
        return h.getRegisteredName();
    }

    /** Rellena las filas [j0, j1) del cuadro. */
    private void filas(byte[] fuera, int x0, int z0, int n, int paso, int y, int j0, int j1) {
        for (int j = j0; j < j1; j++) {
            int z = z0 + j * paso;
            for (int i = 0; i < n; i++) {
                fuera[j * n + i] = (byte) indice(nombreEn(x0 + i * paso, y, z));
            }
        }
    }

    // ─────────────────────────────────────────────── el equipo de trabajadores
    // Cada hilo tiene SU PROPIO generador. Los objetos de ruido de Minecraft
    // llevan cachés dentro y compartirlos entre hilos es pedir una carrera de
    // datos: el mapa saldría casi bien, con puntos sueltos mal, que es
    // exactamente el tipo de fallo que no se ve hasta que ya no te fías de nada.
    // Arrancar un generador de más cuesta ~0,7 s una sola vez.
    static final class Equipo {
        final List<Biomas> obreros = new ArrayList<>();
        final ExecutorService pool;

        Equipo(HolderLookup.Provider registros, long semilla, int n) {
            for (int i = 0; i < n; i++) obreros.add(new Biomas(registros, semilla));
            this.pool = Executors.newFixedThreadPool(n);
        }

        int hilos() { return obreros.size(); }

        Biomas uno() { return obreros.get(0); }

        byte[] cuadro(int x0, int z0, int n, int paso, int y) {
            byte[] fuera = new byte[n * n];
            int h = Math.min(obreros.size(), Math.max(1, n / 16));
            if (h == 1) {
                obreros.get(0).filas(fuera, x0, z0, n, paso, y, 0, n);
                return fuera;
            }
            List<Future<?>> tareas = new ArrayList<>();
            for (int k = 0; k < h; k++) {
                final int j0 = n * k / h, j1 = n * (k + 1) / h;
                final Biomas b = obreros.get(k);
                tareas.add(pool.submit(() -> b.filas(fuera, x0, z0, n, paso, y, j0, j1)));
            }
            for (Future<?> f : tareas) {
                try { f.get(); } catch (Exception e) { throw new RuntimeException(e); }
            }
            return fuera;
        }
    }

    /** Un número que resume CÓMO reparte los biomas este generador.
     *
     * Los azulejos que dibuja el panel se guardan para siempre porque la
     * semilla no cambia. Pero una versión nueva de Minecraft sí podría cambiar
     * el reparto de biomas, y entonces lo guardado estaría mal sin que nadie se
     * entere — el mapa seguiría enseñando el mundo de la versión de antes.
     *
     * Esto muestrea una rejilla fija y la resume en un número. El panel lo mete
     * en el nombre de la carpeta del caché: si cambia, se vuelve a dibujar solo,
     * y si no cambia, no se pierde nada de lo ya hecho. */
    public String huella() {
        long h = 1125899906842597L;
        for (int z = -8192; z <= 8192; z += 512)
            for (int x = -8192; x <= 8192; x += 512)
                h = h * 31 + nombreEn(x, Y_SUPERFICIE, z).hashCode();
        return Long.toHexString(h & 0xFFFFFFFFL);
    }

    public static String leyendaJson() {
        StringBuilder sb = new StringBuilder("{");
        for (int i = 0; i < NOMBRES.size(); i++) {
            if (i > 0) sb.append(",");
            sb.append('"').append(i).append("\":\"").append(NOMBRES.get(i)).append('"');
        }
        return sb.append("}").toString();
    }

    // ────────────────────────────────────────────────────────── el servicio
    private static void responder(HttpExchange ex, int codigo, String tipo, byte[] cuerpo)
            throws java.io.IOException {
        ex.getResponseHeaders().set("Content-Type", tipo);
        ex.sendResponseHeaders(codigo, cuerpo.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(cuerpo);
        }
    }

    private static Map<String, String> parametros(HttpExchange ex) {
        Map<String, String> m = new HashMap<>();
        String q = ex.getRequestURI().getRawQuery();
        if (q != null) {
            for (String p : q.split("&")) {
                int i = p.indexOf('=');
                if (i > 0) m.put(p.substring(0, i), p.substring(i + 1));
            }
        }
        return m;
    }

    private static int entero(Map<String, String> m, String k, int porDefecto) {
        try {
            return Integer.parseInt(m.get(k));
        } catch (Exception e) {
            return porDefecto;
        }
    }

    private static boolean tiene(String[] args, String bandera) {
        for (String a : args) if (a.equals(bandera)) return true;
        return false;
    }

    private static int valor(String[] args, String bandera, int porDefecto) {
        for (int i = 0; i + 1 < args.length; i++)
            if (args[i].equals(bandera)) {
                try { return Integer.parseInt(args[i + 1]); } catch (Exception e) { return porDefecto; }
            }
        return porDefecto;
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.out.println("uso: Biomas <semilla> [--servicio] [--puerto N] [--hilos N]");
            System.out.println("     Biomas <semilla> --comprobar-hilos");
            System.out.println("     Biomas <semilla> x z y [x z y ...]");
            return;
        }
        long semilla = Long.parseLong(args[0]);
        int puerto = valor(args, "--puerto", 25580);
        int hilos = valor(args, "--hilos", Math.max(1, Runtime.getRuntime().availableProcessors() - 1));

        long t0 = System.currentTimeMillis();
        SharedConstants.tryDetectVersion();
        Bootstrap.bootStrap();
        long tBoot = System.currentTimeMillis() - t0;
        t0 = System.currentTimeMillis();
        HolderLookup.Provider registros = VanillaRegistries.createLookup();
        numerarBiomas(registros);
        Equipo equipo = new Equipo(registros, semilla, hilos);
        long tGen = System.currentTimeMillis() - t0;
        System.err.println("[Minecraft arrancado en " + tBoot + " ms; " + hilos
                + " generador(es) en " + tGen + " ms; " + NOMBRES.size() + " biomas]");

        // ── comprobación de hilos ────────────────────────────────────────
        // Un generador por hilo debería dar exactamente lo mismo que uno solo.
        // Si no, el reparto está mal y más vale saberlo aquí que descubrirlo
        // como manchas raras en el mapa dentro de tres semanas.
        if (tiene(args, "--comprobar-hilos")) {
            int n = 256, paso = 16;
            byte[] muchos = equipo.cuadro(-2048, -2048, n, paso, Y_SUPERFICIE);
            byte[] uno = new byte[n * n];
            equipo.uno().filas(uno, -2048, -2048, n, paso, Y_SUPERFICIE, 0, n);
            boolean igual = Arrays.equals(uno, muchos);
            System.out.println((igual ? "IGUAL" : "DISTINTO") + " · " + hilos
                    + " hilos vs 1 · " + (n * n) + " puntos");
            if (!igual) {
                int d = 0;
                for (int i = 0; i < uno.length; i++) if (uno[i] != muchos[i]) d++;
                System.out.println("  " + d + " puntos distintos");
            }
            return;
        }

        if (!tiene(args, "--servicio")) {
            List<int[]> puntos = new ArrayList<>();
            for (int i = 1; i + 2 < args.length; i++) {
                if (args[i].startsWith("--")) continue;
                try {
                    puntos.add(new int[]{Integer.parseInt(args[i]),
                                         Integer.parseInt(args[i + 1]),
                                         Integer.parseInt(args[i + 2])});
                    i += 2;
                } catch (NumberFormatException e) { /* no era un punto */ }
            }
            if (puntos.isEmpty()) {
                int[][] p = {{0, 0}, {1000, 1000}, {17752, -14598}, {8192, -18432}, {24576, -10240}};
                for (int[] q : p)
                    System.out.println(q[0] + ", " + q[1] + "  →  "
                            + equipo.uno().nombreEn(q[0], Y_SUPERFICIE, q[1]));
                t0 = System.currentTimeMillis();
                equipo.cuadro(0, 0, 256, 4, Y_SUPERFICIE);
                System.out.println("azulejo de 256x256 en "
                        + (System.currentTimeMillis() - t0) + " ms con " + hilos + " hilo(s)");
            } else {
                for (int[] q : puntos)
                    System.out.println(q[0] + " " + q[1] + " (y=" + q[2] + ")  →  "
                            + equipo.uno().nombreEn(q[0], q[2], q[1]));
            }
            return;
        }

        // ── el servicio ──────────────────────────────────────────────────
        // Solo escucha en 127.0.0.1: al panel le llega por dentro de la
        // máquina y no hay que abrir ningún puerto al mundo.
        HttpServer srv = HttpServer.create(new InetSocketAddress("127.0.0.1", puerto), 0);
        final Equipo eq = equipo;

        final String huella = equipo.uno().huella();
        System.err.println("[huella del generador: " + huella + "]");
        srv.createContext("/salud", ex ->
                responder(ex, 200, "application/json",
                        ("{\"ok\":true,\"semilla\":" + semilla + ",\"y\":" + Y_SUPERFICIE
                                + ",\"hilos\":" + eq.hilos() + ",\"biomas\":" + NOMBRES.size()
                                + ",\"huella\":\"" + huella + "\"}")
                                .getBytes(StandardCharsets.UTF_8)));

        srv.createContext("/leyenda", ex ->
                responder(ex, 200, "application/json",
                        leyendaJson().getBytes(StandardCharsets.UTF_8)));

        srv.createContext("/bioma", ex -> {
            Map<String, String> p = parametros(ex);
            int x = entero(p, "x", 0), z = entero(p, "z", 0);
            int y = entero(p, "y", Y_SUPERFICIE);
            String n = eq.uno().nombreEn(x, y, z);
            responder(ex, 200, "application/json",
                    ("{\"x\":" + x + ",\"z\":" + z + ",\"y\":" + y
                            + ",\"bioma\":\"" + n + "\",\"id\":" + indice(n) + "}")
                            .getBytes(StandardCharsets.UTF_8));
        });

        // Muchos puntos sueltos de una vez: para decidir si una estructura
        // candidata cae en un bioma donde de verdad puede generarse. Pedirlos
        // uno a uno serían miles de peticiones.
        //  ?p=x,z;x,z;x,z…   →  un byte por punto
        srv.createContext("/puntos", ex -> {
            Map<String, String> p = parametros(ex);
            int y = entero(p, "y", Y_SUPERFICIE);
            String lista = p.getOrDefault("p", "");
            String[] partes = lista.isEmpty() ? new String[0] : lista.split(";");
            byte[] fuera = new byte[partes.length];
            for (int i = 0; i < partes.length; i++) {
                int c = partes[i].indexOf(',');
                if (c <= 0) continue;
                try {
                    int x = Integer.parseInt(partes[i].substring(0, c));
                    int z = Integer.parseInt(partes[i].substring(c + 1));
                    fuera[i] = (byte) indice(eq.uno().nombreEn(x, y, z));
                } catch (NumberFormatException e) { fuera[i] = (byte) 255; }
            }
            responder(ex, 200, "application/octet-stream", fuera);
        });

        // Un cuadro de n×n muestras. Devuelve un byte por punto: el número del
        // bioma según la leyenda fija, que el panel pide una vez y guarda.
        srv.createContext("/cuadro", ex -> {
            Map<String, String> p = parametros(ex);
            int x = entero(p, "x", 0), z = entero(p, "z", 0);
            int n = Math.min(1024, Math.max(1, entero(p, "n", 256)));
            int paso = Math.max(1, entero(p, "paso", 4));
            int y = entero(p, "y", Y_SUPERFICIE);
            responder(ex, 200, "application/octet-stream", eq.cuadro(x, z, n, paso, y));
        });

        srv.setExecutor(Executors.newFixedThreadPool(2));
        srv.start();
        System.err.println("[escuchando en 127.0.0.1:" + puerto + "]");
        System.out.println("LISTO " + puerto);
        System.out.flush();
    }
}
