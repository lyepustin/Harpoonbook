# Harpoonbook

Cuaderno de pesca submarina para el grupo.

Harpoonbook junta en un solo sitio lo que miramos antes de entrar al agua (marea, solunar, viento, olas, corrientes, visibilidad) y lo que aprendemos después de cada salida. La idea es tener menos dudas al elegir sitio y método, y más memoria de lo que ha funcionado.

No hace falta ser técnico para entenderlo ni para usarlo.

## ¿Para qué sirve?

Antes de una salida, todos hacemos más o menos lo mismo: mirar el viento, las olas, la marea, la hora buena según la luna, y luego decidir dónde ir y cómo pescar (espera, acecho, agujero, rompientes, mar adentro).

Harpoonbook hace tres cosas:

1. Te muestra las condiciones del mar para los sitios que tengáis dados de alta (calas, espigones, rompientes…).
2. Te estima la visibilidad del agua según mar de fondo, oleaje, viento, marea, memoria de los últimos días y tipo de fondo del sitio.
3. Cuando vuelves del agua, registras la salida: dirección de pesca, cómo estaba el agua, apneas y lo que viste/pescaste.

Con el tiempo eso se convierte en historial del grupo: qué días salieron bien, en qué sitio y con qué técnica.

## ¿Qué ves en la app?

### Panel principal

Vista de decisión: arriba lo importante; el detalle se despliega si lo necesitas.

1. **Foto/mapa del spot** y ficha corta (fondo, costa, exposición, direcciones).
2. **Ahora:** visibilidad (etiqueta + score + metros), marea, corriente (fuerza + dirección), mejor hora de entrada y mar cercano (viento/olas/swell de la franja más próxima).
3. **Detalle bajo demanda** (expanders): solunar completo, tabla Windy, visibilidad por horas, contexto de los últimos 3 días, corrientes, historial y raw técnico.

Puedes cambiar de sitio y de día en el menú lateral. Si miras días pasados, ves lo que se guardó ese día; si miras hoy, puedes actualizar los datos con un botón.

![Pantalla principal con condiciones y visibilidad](docs/images/pantalla-condiciones.png)
_Captura de pantalla: condiciones del mar y previsión de visibilidad._

### Registrar una salida

En el menú lateral → **Registro de salida**:

- Hora inicio/fin y sitio
- Dirección de pesca (las definidas en el spot)
- Agua: temperatura, visibilidad, corriente, turbidez (con preset desde snapshots, editable)
- Apneas: sensación, promedio, profundidad, compensación
- Avistamientos: hasta 8 especies vistas / intentadas / pescadas

También puedes **editar** una salida anterior si se te olvidó algo.

![Formulario para registrar una salida](docs/images/registro-salida.png)
_Captura de pantalla: registrar cómo fue la salida._

### Sitios de pesca

Desde el menú lateral puedes gestionar spots: coordenadas, captura de Google Maps/Earth, ficha (prompt ChatGPT para rellenar), orientaciones y direcciones de pesca.

La marea y el solunar se toman de una referencia costera (Cabo Roig); el resto se calcula por coordenadas del sitio.

## Métodos de pesca

| Método              | En pocas palabras                              |
| ------------------- | ---------------------------------------------- |
| Espera              | Quedarte en un sitio y esperar que pase el pez |
| Acecho              | Moverte buscando activamente                   |
| Agujero             | Trabajar cuevas y huecos                       |
| Cerca de rompientes | Zona de oleaje / rompiente                     |
| Mar adentro         | Alejarte de la costa                           |

## Cómo lo usamos en el día a día

1. Antes de salir: abrimos Harpoonbook, elegimos sitio, miramos solunar, viento, corrientes, contexto reciente y visibilidad.
2. Si hace falta: actualizamos datos del día (botón **Actualizar** captura todos los sitios; o `python -m harpoonbook`).
3. Después de pescar: registramos la salida con nota y notas.
4. De vez en cuando: repasamos el historial para ver patrones ("los días de marea baja en tal sitio…").

## Documentación

Detalle de fuentes de datos, captura y lógica de visibilidad en **[docs/](docs/README.md)**.

## Fotos del proyecto

Sube las capturas a la carpeta `docs/images/` con estos nombres:

- `pantalla-condiciones.png` — vista del panel con condiciones y visibilidad
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

**Copernicus (opcional):** claridad del agua (Secchi base) requiere cuenta gratuita en [Copernicus Marine](https://data.marine.copernicus.eu/register) y `copernicusmarine login`. Sin eso, Windy, tablademareas y Open-Meteo siguen funcionando.

**Captura automática** (recomendada, 1-2 veces al día):

```bash
python -m harpoonbook
```

Programar como Tarea programada de Windows en el equipo que tenga el login de Copernicus.

Los datos del grupo se guardan en `data/harpoonbook.db` y van incluidos en el repositorio para que todos partamos del mismo historial.

</details>

## Estado del proyecto

Versión inicial. Las condiciones se recogen de fuentes públicas y la previsión de visibilidad es orientativa: la última palabra la tiene el mar y vuestra experiencia. Con cada salida que registréis, Harpoonbook irá siendo más útil para el grupo.
