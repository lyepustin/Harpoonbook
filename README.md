# Harpoonbook

Cuaderno de pesca submarina para el grupo.

Harpoonbook junta en un solo sitio lo que miramos antes de entrar al agua (marea, solunar, viento, olas) y lo que aprendemos después de cada salida. La idea es tener menos dudas al elegir sitio y método, y más memoria de lo que ha funcionado.

No hace falta ser técnico para entenderlo ni para usarlo.

## ¿Para qué sirve?

Antes de una salida, todos hacemos más o menos lo mismo: mirar el viento, las olas, la marea, la hora buena según la luna, y luego decidir dónde ir y cómo pescar (espera, acecho, agujero, rompientes, mar adentro).

Harpoonbook hace tres cosas:

1. Te muestra las condiciones del mar para los sitios que tengáis dados de alta (calas, espigones, rompientes…).
2. Te sugiere qué método tiene más sentido según esas condiciones y lo que ya sabemos de cada sitio.
3. Cuando vuelves del agua, registras cómo te fue: nota del 0 al 10, método, visibilidad, si viste pulpos, notas rápidas.

Con el tiempo eso se convierte en historial del grupo: qué días salieron bien, en qué sitio y con qué técnica.

## ¿Qué ves en la app?

### Panel principal

Ahí aparece lo que miramos antes de mojarnos:

- Solunar y marea: mejores franjas para entrar al agua, actividad del día, fase lunar.
- Viento y olas (Windy): tabla por franjas de 3 horas, con colores para leer rápido si hay viento fuerte u olas incómodas.
- Recomendaciones: las dos técnicas que mejor encajan ahora en el sitio elegido, con una puntuación y el porqué.

Puedes cambiar de sitio y de día en el menú lateral. Si miras días pasados, ves lo que se guardó ese día; si miras hoy, puedes actualizar los datos con un botón.

![Pantalla principal con condiciones y recomendaciones](docs/images/pantalla-condiciones.png)
*Captura de pantalla: condiciones del mar y recomendaciones para un sitio.*

### Registrar una salida

Después de pescar, rellenas un formulario corto:

- Hora y sitio
- Método que usaste
- Nota del 0 al 10
- Visibilidad, pulpos (opcional)
- Unas notas libres ("agua clara, corriente suave, vi un pulpo en la roca de la derecha")

No tiene que ser perfecto. Cuanto más registremos, mejor aprenderá el sistema qué nos funciona.

![Formulario para registrar una salida](docs/images/registro-salida.png)
*Captura de pantalla: registrar cómo fue la salida.*

### Sitios de pesca

Desde el menú lateral puedes ver y gestionar los puntos que tengáis dados de alta (calas, espigones, coordenadas). Cada grupo configura los suyos; si añadís un sitio nuevo, entra aquí.

La marea y el solunar se toman de una referencia costera que configuréis; el viento y las olas se calculan para las coordenadas de cada sitio.

## Métodos que comparamos

| Método | En pocas palabras |
|--------|-------------------|
| Espera | Quedarte en un sitio y esperar que pase el pez |
| Acecho | Moverte buscando activamente |
| Agujero | Trabajar cuevas y huecos |
| Cerca de rompientes | Zona de oleaje / rompiente |
| Mar adentro | Alejarte de la costa |

Las recomendaciones combinan el estado del mar con lo que históricamente encaja mejor en cada sitio.

## Cómo lo usamos en el día a día

1. Antes de salir: abrimos Harpoonbook, elegimos sitio, miramos solunar, viento y recomendación.
2. Si hace falta: actualizamos datos del día (botón Actualizar o captura Windy para todos los sitios).
3. Después de pescar: registramos la salida con nota y notas.
4. De vez en cuando: repasamos el historial para ver patrones ("los días de marea baja en tal sitio…").

## Fotos del proyecto

Sube las capturas a la carpeta `docs/images/` con estos nombres:

- `pantalla-condiciones.png` — vista del panel con condiciones y recomendaciones
- `registro-salida.png` — formulario de registro tras una salida

Los enlaces de arriba se verán solos cuando subas las imágenes.

## Para quien quiera arrancarlo en el ordenador

<details>
<summary>Instrucciones técnicas (clic para expandir)</summary>

Requisitos: Python 3.10+ instalado.

```bash
git clone https://github.com/lyepustin/Harpoonbook.git
cd Harpoonbook
pip install -r requirements.txt
streamlit run app.py
```

Se abrirá en el navegador (por defecto `http://localhost:8501`).

Los datos del grupo se guardan en `data/harpoonbook.db` y van incluidos en el repositorio para que todos partamos del mismo historial.

</details>

## Estado del proyecto

Versión inicial. Las condiciones se recogen de fuentes públicas (tabla de mareas, Windy) y las sugerencias son orientativas: la última palabra la tiene el mar y vuestra experiencia. Con cada salida que registréis, Harpoonbook irá siendo más útil para el grupo.
