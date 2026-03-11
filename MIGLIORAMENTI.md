# Miglioramenti proposti — cxf2shp_vestito

## Prioritari

### 1. Robustezza del Parser CXF
- **Gestione errori silenziosa** (`_d.py:478`): i `except Exception: pass/i+=1` ingoiano errori senza log. Aggiungere logging dell'eccezione e della riga problematica.
- **Encoding**: riga 429, `errors='ignore'` può scartare caratteri CXF validi. Verificare se `latin-1` è più corretto di `ascii`.
- **Recupero dopo eccezione nel blocco BORDO**: in caso di eccezione, `i += 1` può far saltare blocchi interi invece di recuperare correttamente.

### 2. Identificazione tipo BORDO (`_d.py:459-466`)
Il riconoscimento di `ACQUA`, `STRADA`, `+` è basato su `in`/`endswith` su stringhe libere — fragile. Se l'Agenzia delle Entrate cambia convenzione, il parsing si rompe silenziosamente (tutto finisce in Particelle).

### 3. Thread safety (`_d.py:789`)
`_on_finished` accede a `self.thread._layers` dal main thread mentre il thread potrebbe non essere ancora completamente terminato. I dati dovrebbero essere passati tramite segnale.

### 4. CRS e coordinate sorgente — Cassini-Soldner vs Monte Mario
`EPSG:3003` fisso non copre tutta Italia (il sud usa `EPSG:3004`), ma il problema più profondo riguarda il sistema di proiezione dei file CXF sorgente.

**Due famiglie di CXF:**

| Tipo | Periodo | Coordinate tipiche | CRS |
|---|---|---|---|
| Moderno | ~2010 → oggi | X ≈ 1.400.000–1.900.000 | Monte Mario EPSG:3003/3004 |
| Storico | precedente | X ≈ −5.000 a +5.000 | Cassini-Soldner locale |

**Cassini-Soldner** è un sistema locale di proiezione usato storicamente dal catasto italiano: le coordinate sono espresse in metri rispetto a un **punto di origine diverso per ogni Comune**. Non esiste un singolo EPSG per tutti i Comuni.

Il plugin attualmente copia le coordinate raw dal CXF senza trasformazione. Se il file è in Cassini-Soldner, lo shapefile output avrà coordinate locali (piccoli valori ±migliaia) dichiarate come EPSG:3003 → i layer appaiono vicino a **NULL Island**.

**Fix implementato (v1.2):** selettore CRS sorgente nella UI + `options.destCRS` esplicito per garantire il `.prj` corretto. Funziona per CXF già in Monte Mario.

**Fix mancante (da implementare — Priorità Alta):** supporto Cassini-Soldner:
1. Leggere il codice Comune dal blocco `MAPPA` del CXF (già parsato: campo `COMUNE`)
2. Cercare l'origine Cassini-Soldner del Comune in una tabella di lookup (dati pubblici)
3. Applicare la traslazione: `X_MM = X_origine + X_CS`, `Y_MM = Y_origine + Y_CS`
4. Opzionalmente riproiettare in un CRS target scelto dall'utente

La tabella delle origini comunali è disponibile nei dati tecnici dell'Agenzia delle Entrate.

---

## Funzionali

### 5. Selezione file CXF singoli
Ora si seleziona solo la cartella. Aggiungere la possibilità di selezionare file CXF specifici.

### 6. Pulsante Annulla durante la conversione
Manca un pulsante **Annulla**. Il `closeEvent` interrompe il thread, ma chiudere la finestra non è intuitivo come azione di annullamento.

### 7. Output GeoPackage
Aggiungere opzione per salvare come **GeoPackage** (`.gpkg`) invece di Shapefile:
- file unico
- supporto UTF-8 nativo
- nessun limite 2 GB
- nomi campo senza troncatura a 10 caratteri

### 8. Riepilogo post-conversione
Al termine mostrare un riepilogo: numero di feature per layer ed eventuali warning di parsing.

---

## Minori

### 9. Stili QML inline → file esterni (`_d.py:32-341`)
I QML embedded nel codice rendono il file pesante (~340 righe) e difficili da modificare. Meglio come file `.qml` separati nella cartella del plugin, caricati a runtime.

### 10. Ordinamento layer nel gruppo QGIS (`_d.py:814`)
Usa `insertLayer(0, ...)` in loop: i layer vengono inseriti in ordine inverso rispetto a `ORDERED_KEYS`. Funziona per caso ma è poco chiaro e fragile.

---

## Riepilogo

| # | Area | Priorità | Difficoltà |
|---|------|----------|------------|
| 1 | Parser — gestione errori | Alta | Bassa |
| 2 | Parser — identificazione BORDO | Alta | Media |
| 3 | Thread safety | Alta | Bassa |
| 4a | CRS selezionabile nella UI | ~~Media~~ Fatto | ~~Media~~ |
| 4b | Supporto Cassini-Soldner → Monte Mario | Alta | Alta |
| 5 | Selezione file singoli | Media | Bassa |
| 6 | Pulsante Annulla | Media | Bassa |
| 7 | Output GeoPackage | Media | Media |
| 8 | Riepilogo post-conversione | Bassa | Bassa |
| 9 | QML come file esterni | Bassa | Bassa |
| 10 | Ordinamento layer gruppo | Bassa | Bassa |
