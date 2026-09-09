package califree;

// Le pregunta los biomas al generador de mundos DE MINECRAFT, el que viene
// dentro de tu server.jar. No reimplementa nada: monta el mismo BiomeSource que
// usaría el servidor para tu semilla y le pregunta punto por punto.
//
// POR QUÉ SE PUEDE HACER ASÍ
// El jar de la 26.2 viene SIN OFUSCAR (7.434 de 7.445 clases con su nombre
// real), y por eso Mojang ha dejado de publicar las tablas de equivalencias: ya
// no hacen falta. Esto se compila contra el jar como contra cualquier librería.
//
// NO GENERA CHUNKS NI ESCRIBE NADA. Solo hace la cuenta de qué bioma toca en
// cada sitio, que es exactamente lo que hace Chunkbase — pero con el generador
// de verdad en vez de con una reimplementación, así que no puede desviarse ni
// quedarse atrás cuando salga una versión nueva.
//
// Firmas sacadas del jar real con scripts/api-jar.py, no de memoria:
//   VanillaRegistries.createLookup()                     → HolderLookup.Provider
//   MultiNoiseBiomeSource.createFromPreset(Holder)       → MultiNoiseBiomeSource
//   RandomState.create(HolderGetter.Provider, ResourceKey, long) → RandomState
//   RandomState.sampler()                                → Climate.Sampler
//   BiomeSource.getNoiseBiome(int,int,int,Climate.Sampler) → Holder
//   Holder.getRegisteredName()                           → String

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

public class Biomas {

    private final MultiNoiseBiomeSource fuente;
    private final Climate.Sampler muestreador;

    public Biomas(long semilla) {
        HolderLookup.Provider registros = VanillaRegistries.createLookup();

        // El preset del overworld: la misma lista de parámetros de clima que
        // usa un mundo normal. Si algún día se quiere el nether, aquí va NETHER.
        HolderGetter<net.minecraft.world.level.biome.MultiNoiseBiomeSourceParameterList> presets =
                registros.lookupOrThrow(Registries.MULTI_NOISE_BIOME_SOURCE_PARAMETER_LIST);
        this.fuente = MultiNoiseBiomeSource.createFromPreset(
                presets.getOrThrow(MultiNoiseBiomeSourceParameterLists.OVERWORLD));

        // Aquí es donde entra la semilla: RandomState es lo que convierte la
        // semilla en los ruidos concretos de este mundo.
        RandomState estado = RandomState.create(
                registros, NoiseGeneratorSettings.OVERWORLD, semilla);
        this.muestreador = estado.sampler();
    }

    /** El bioma en unas coordenadas de BLOQUE. Devuelve "minecraft:plains". */
    public String en(int x, int y, int z) {
        // getNoiseBiome trabaja en cuartos de bloque (el mundo guarda los
        // biomas en celdas de 4×4×4), así que hay que dividir entre 4. Pasarle
        // coordenadas de bloque sin dividir da un mapa 4 veces más grande, que
        // se ve plausible y está mal.
        Holder<?> h = fuente.getNoiseBiome(x >> 2, y >> 2, z >> 2, muestreador);
        return h.getRegisteredName();
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.out.println("uso: Biomas <semilla> [x z y x z y ...]");
            return;
        }
        long semilla = Long.parseLong(args[0]);

        long t0 = System.currentTimeMillis();
        SharedConstants.tryDetectVersion();
        Bootstrap.bootStrap();
        System.err.println("[arranque de Minecraft: " + (System.currentTimeMillis() - t0) + " ms]");

        t0 = System.currentTimeMillis();
        Biomas b = new Biomas(semilla);
        System.err.println("[generador listo: " + (System.currentTimeMillis() - t0) + " ms]");

        if (args.length >= 4) {
            for (int i = 1; i + 2 < args.length; i += 3) {
                int x = Integer.parseInt(args[i]);
                int z = Integer.parseInt(args[i + 1]);
                int y = Integer.parseInt(args[i + 2]);
                System.out.println(x + " " + z + " (y=" + y + ")  →  " + b.en(x, y, z));
            }
        } else {
            // Prueba de fábrica: unos cuantos puntos y una medida de velocidad,
            // que es lo que decide si esto puede servir azulejos de mapa.
            int[][] puntos = {
                {0, 0}, {1000, 1000}, {17752, -14598}, {8192, -18432}, {24576, -10240},
            };
            for (int[] p : puntos) {
                System.out.println(p[0] + ", " + p[1] + "  →  " + b.en(p[0], 64, p[1]));
            }
            System.out.println();
            t0 = System.currentTimeMillis();
            int n = 0;
            for (int x = 0; x < 640; x += 4) {
                for (int z = 0; z < 640; z += 4) {
                    b.en(17752 + x, 64, -14598 + z);
                    n++;
                }
            }
            long ms = System.currentTimeMillis() - t0;
            System.out.println(n + " puntos en " + ms + " ms  ("
                    + (ms == 0 ? "muy rápido" : (n * 1000L / ms) + " por segundo") + ")");
            System.out.println("un azulejo de 256x256 píxeles = 65.536 puntos");
        }
    }
}
