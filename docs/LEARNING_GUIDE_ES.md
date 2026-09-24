# Guía de aprendizaje para Daniel

**ETL.** Cada fila del log procesado es una lectura. Antes de agruparla, comprobamos profundidad, máscara cuando existe y si el grupo TSG es diagnóstico. El reporte separa motivos primarios para que la suma sea exactamente el número de filas recibidas. Dos lecturas a la misma profundidad se conservan con `source_row` distintos. El intervalo de 1 m resume las etiquetas admitidas y deja visible cuántas filas no participaron. Un metro de bin no implica un metro de testigo observado.

**Features.** Si en un intervalo hay dos lecturas admitidas, una `A` y una `B`, sus frecuencias serán 0,5 y 0,5. Esas cifras describen las respuestas de TSG, no porcentajes de masa mineral. `Aspectral` es una interpretación no diagnóstica, no equivale por sí misma a no-roca.

**PCA y clustering.** PCA cambia la representación a ejes de variación; no asigna grupos. K-Means sí asigna cada intervalo al centro más cercano. En este primer runner se aplica raíz cuadrada a las frecuencias y K-Means sin PCA. El número del cluster es una etiqueta interna, no un tipo de roca. La distancia al centro mide parecido en esa representación, no confianza ni probabilidad de mineralización.

**Qué falta para afirmar algo geológico.** Hay que comparar grupos con ensayos/litología alineados por profundidad y pedir revisión experta. También hay que comprobar estabilidad frente a sondajes retirados, ancho del intervalo, semilla, instrumento y procesamiento. Los logs TSG y los espectros relacionados no proporcionan dos verdades independientes.

**Ejemplo real en todas las capas.** En 05GJD001 SWIR, 150–151 m, entran 169 lecturas; 56 tienen una etiqueta diagnóstica y 113 no. Las frecuencias entre esas 56 son DARK-MICA 62,5%, CHLORITE 28,6% y CARBONATE 8,9%. El producto es `60df8d254d8440036ce3` y el intervalo `bbadb96c5a5b14505088e700`; el informe QC, Parquet, PostgreSQL, API, perfil y CSV muestran la misma identidad. La máscara Final Mask falta en el log procesado, así que no podemos afirmar que estas 56 lecturas pasaron esa máscara. El grupo 0 y la distancia 0,803 describen el modelo, no la ley mineral ni una probabilidad.

**Inferencia frente a entrenamiento.** Entrenar elige k y ajusta centros con sondajes de desarrollo; el último sondaje se reserva para una comprobación interna. Inferir toma un modelo congelado, exige las mismas variables y versiones de librerías, y asigna intervalos sin cambiar los centros. La prueba local de inferencia en 05GJD001 comprobó el mecanismo, pero ese sondaje ya se había usado para desarrollo. Para demostrar generalización hace falta un sondaje nuevo independiente.

Preguntas de comprensión preparadas: ¿qué se excluyó y por qué? ¿qué representa un intervalo? ¿qué hace PCA? ¿qué significa pertenecer a un cluster? ¿por qué una anomalía necesita revisión? La prueba con una persona del equipo sigue pendiente.
