# -*- coding: utf-8 -*-
"""
cxf2shp_vestito_d.py
Finestra di dialogo e logica di conversione del plugin CXF to Shape Vestito.
Autore: Fortunato Amore
"""

import csv
import os
import traceback

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QLineEdit, QFileDialog,
    QMessageBox, QProgressBar, QGroupBox, QCheckBox,
    QTextEdit, QTextBrowser, QSizePolicy, QFrame, QComboBox, QWidget
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QCoreApplication, QVariant
from qgis.PyQt.QtGui import QFont, QColor, QPalette

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
    QgsField, QgsVectorFileWriter, QgsCoordinateReferenceSystem,
    QgsCoordinateTransformContext, QgsCoordinateTransform
)
from qgis.gui import QgsProjectionSelectionWidget, QgsCollapsibleGroupBox

# Compatibilità Qt5/Qt6: QMetaType sostituisce QVariant.Type in PyQt6
try:
    from qgis.PyQt.QtCore import QMetaType
    _F_STR = QMetaType.Type.QString
    _F_DBL = QMetaType.Type.Double
    _F_INT = QMetaType.Type.Int
except (ImportError, AttributeError):
    _F_STR = QVariant.String
    _F_DBL = QVariant.Double
    _F_INT = QVariant.Int


# =====================================================================
# CARICAMENTO STILI QML DA FILE ESTERNI
# =====================================================================

_STYLES_DIR = os.path.join(os.path.dirname(__file__), "styles")


def _load_qml_styles():
    """Carica gli stili QML dalla cartella styles/ accanto al plugin."""
    styles = {}
    if not os.path.isdir(_STYLES_DIR):
        return styles
    for fname in os.listdir(_STYLES_DIR):
        if fname.endswith(".qml"):
            name = fname[:-4]
            with open(os.path.join(_STYLES_DIR, fname), "r", encoding="utf-8") as f:
                styles[name] = f.read()
    return styles


QML_STYLES = _load_qml_styles()


# =====================================================================
# LOOKUP CASSINI-SOLDNER
# =====================================================================

_CS_CSV = os.path.join(os.path.dirname(__file__), "Italia_CS_PRJ_srtext.csv")


def _load_cs_lookup():
    """Carica le origini Cassini-Soldner comunali dal CSV bundled nel plugin.
    Restituisce (lookup_dict, duplicates_dict)."""
    lookup = {}
    duplicates = {}
    if not os.path.isfile(_CS_CSV):
        return lookup, duplicates
    with open(_CS_CSV, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cod = row["CodCata"]
            if cod in lookup:
                duplicates[cod] = duplicates.get(cod, 1) + 1
            lookup[cod] = row["srtext"]
    return lookup, duplicates


CS_LOOKUP, CS_DUPLICATES = _load_cs_lookup()


# =====================================================================
# AUTODETECT CRS DA COORDINATE CXF (Roma 40: EPSG:3003 / EPSG:3004)
# =====================================================================

def _detect_roma40_crs(file_path):
    """Rileva automaticamente EPSG:3003 o EPSG:3004 leggendo la prima
    coordinata X del file CXF.

    Logica: le due zone Gauss-Boaga hanno fasce X non sovrapposte:
      - EPSG:3003 (zona Ovest, meridiano 9°E, false easting 1.500.000):
            X ≈ 1.100.000 – 1.900.000 m
      - EPSG:3004 (zona Est,  meridiano 15°E, false easting 2.520.000):
            X ≈ 2.100.000 – 2.900.000 m
    Soglia di separazione: 2.000.000 m.

    Restituisce 'EPSG:3003', 'EPSG:3004', oppure None se non rilevato.
    """
    try:
        with open(file_path, 'r', encoding='ascii', errors='ignore') as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    val = float(line)
                except ValueError:
                    continue
                if val > 2_000_000:
                    return 'EPSG:3004'
                if val > 1_000_000:
                    return 'EPSG:3003'
    except OSError:
        pass
    return None


# =====================================================================
# THREAD DI CONVERSIONE
# =====================================================================

class ConversionThread(QThread):
    """Thread per la conversione CXF → Shapefile/GeoPackage (non blocca la GUI)."""

    progress = pyqtSignal(int)           # 0-100
    log = pyqtSignal(str)                # messaggio di log
    finished = pyqtSignal(bool, str)     # successo, messaggio finale

    def __init__(self, folder_path, output_dir, output_format, load_in_qgis, hide_ext_layers,
                 crs_authid="EPSG:3003", use_cassini=False):
        super().__init__()
        self.folder_path = folder_path
        self.output_dir = output_dir
        self.output_format = output_format   # "SHP" o "GPKG"
        self.load_in_qgis = load_in_qgis
        self.hide_ext_layers = hide_ext_layers
        self.crs_authid = crs_authid
        self.use_cassini = use_cassini
        self._layers = None

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_map_name(map_name):
        comune = map_name[0:4]
        sezione = map_name[4:5]
        if sezione == '_':
            sezione = ''
        foglio = map_name[5:9].lstrip('0')
        allegato = map_name[9:10]
        if allegato == '0':
            allegato = ''
        sviluppo = map_name[10:11]
        if sviluppo == '0':
            sviluppo = ''
        return comune, sezione, foglio, allegato, sviluppo

    def _create_memory_layers(self):
        crs = self.crs_authid
        layers = {}
        layer_defs = {
            'Fogli':        ('Polygon',    ['LIVELLO']),
            'Acque':        ('Polygon',    ['LIVELLO']),
            'Strade':       ('Polygon',    ['LIVELLO']),
            'Particelle':   ('Polygon',    ['LIVELLO', 'MAPPALE']),
            'Fabbricati':   ('Polygon',    ['LIVELLO', 'MAPPALE']),
            'Testi':        ('Point',      ['TESTO', 'ALTEZZA', 'ANGOLO']),
            'TestiEst':     ('Point',      ['TESTO', 'ALTEZZA', 'ANGOLO']),
            'Simboli':      ('Point',      ['LIVELLO', 'CODICE', 'ANGOLO']),
            'SimboliEst':   ('Point',      ['LIVELLO', 'CODICE', 'ANGOLO']),
            'LnVestizione': ('LineString', ['LIVELLO', 'CODICE']),
            'LnVestEst':    ('LineString', ['LIVELLO', 'CODICE']),
            'Fiduciali':    ('Point',      ['NUMERO', 'CODICE']),
            'FiducialiEst': ('Point',      ['NUMERO', 'CODICE']),
        }
        for name, (geom_type, extra_fields) in layer_defs.items():
            vl = QgsVectorLayer(f"{geom_type}?crs={crs}", name, "memory")
            pr = vl.dataProvider()
            fields = [
                QgsField("COMUNE",   _F_STR, len=4),
                QgsField("SEZIONE",  _F_STR, len=1),
                QgsField("FOGLIO",   _F_STR, len=4),
                QgsField("ALLEGATO", _F_STR, len=1),
                QgsField("SVILUPPO", _F_STR, len=1),
            ]
            for f in extra_fields:
                if f in ['ALTEZZA', 'ANGOLO']:
                    fields.append(QgsField(f, _F_DBL))
                elif f == 'CODICE':
                    fields.append(QgsField(f, _F_INT))
                else:
                    fields.append(QgsField(f, _F_STR, len=50))
            pr.addAttributes(fields)
            vl.updateFields()
            layers[name] = vl
        return layers

    @staticmethod
    def _tp(x, y, transform):
        """Restituisce QgsPointXY, trasformato se transform non è None."""
        pt = QgsPointXY(float(x), float(y))
        return transform.transform(pt) if transform is not None else pt

    def _parse_cxf_files(self, cxf_files, layers):
        """Legge e interpreta tutti i file CXF."""
        dest_crs = QgsCoordinateReferenceSystem(self.crs_authid)
        total = len(cxf_files)
        for idx, cxf_file in enumerate(cxf_files):
            self.log.emit(f"  Parsing: {cxf_file}")
            file_path = os.path.join(self.folder_path, cxf_file)
            with open(file_path, 'r', encoding='ascii', errors='ignore') as f:
                lines = [line.strip() for line in f.readlines()]

            i, map_name, base_attrs, cs_transform = 0, "", [], None
            while i < len(lines):
                line = lines[i]
                if line.startswith('MAPPA'):
                    map_name = lines[i + 1]
                    base_attrs = list(self._parse_map_name(map_name))
                    comune, sezione, foglio, allegato, sviluppo = base_attrs
                    cod_label = f"Comune={comune}" + (f" Sez={sezione}" if sezione else "") + f" Foglio={foglio}"
                    if allegato:
                        cod_label += f" All={allegato}"
                    if sviluppo:
                        cod_label += f" Sv={sviluppo}"
                    self.log.emit(f"    MAPPA: {map_name}  [{cod_label}]")
                    if self.use_cassini:
                        cod_cata = map_name[0:5].ljust(5, '_')
                        proj_str = CS_LOOKUP.get(cod_cata)
                        if proj_str:
                            src_crs = QgsCoordinateReferenceSystem()
                            src_crs.createFromProj(proj_str)
                            cs_transform = QgsCoordinateTransform(
                                src_crs, dest_crs, QgsCoordinateTransformContext()
                            )
                            self.log.emit(f"    CS: {cod_cata} → {self.crs_authid}  [{proj_str}]")
                        else:
                            cs_transform = None
                            self.log.emit(f"    WARN: origine CS non trovata per '{cod_cata}'")
                    i += 3

                elif line == 'BORDO':
                    try:
                        codice_id = lines[i + 1]
                        num_isole = int(lines[i + 8])
                        tot_v = int(lines[i + 9])
                        ptr = i + 10
                        isole_v = []
                        if num_isole > 0:
                            for j in range(num_isole):
                                isole_v.append(int(lines[ptr]))
                                ptr += 1
                        poligono = []
                        for num_v in [tot_v - sum(isole_v)] + isole_v:
                            ring = []
                            for j in range(num_v):
                                ring.append(self._tp(lines[ptr], lines[ptr + 1], cs_transform))
                                ptr += 2
                            poligono.append(ring)

                        target = 'Particelle'
                        if codice_id == map_name:
                            target = 'Fogli'
                        elif 'ACQUA' in codice_id:
                            target = 'Acque'
                        elif 'STRADA' in codice_id:
                            target = 'Strade'
                        elif codice_id.endswith('+'):
                            target = 'Fabbricati'

                        attrs = base_attrs + (
                            [target.upper()] if target in ['Fogli', 'Acque', 'Strade']
                            else [target.upper(), codice_id.replace('+', '')]
                        )
                        feat = QgsFeature(layers[target].fields())
                        feat.setGeometry(QgsGeometry.fromPolygonXY(poligono))
                        feat.setAttributes(attrs)
                        layers[target].dataProvider().addFeature(feat)
                        i = ptr
                    except Exception:
                        i += 1

                elif line in ['TESTO', 'TESTO\\']:
                    try:
                        target = 'Testi' if line == 'TESTO' else 'TestiEst'
                        feat = QgsFeature(layers[target].fields())
                        feat.setGeometry(QgsGeometry.fromPointXY(
                            self._tp(lines[i + 4], lines[i + 5], cs_transform)))
                        feat.setAttributes(base_attrs + [lines[i + 1], float(lines[i + 2]), float(lines[i + 3])])
                        layers[target].dataProvider().addFeature(feat)
                    except Exception:
                        pass
                    i += 6

                elif line in ['SIMBOLO', 'SIMBOLO\\']:
                    try:
                        target = 'Simboli' if line == 'SIMBOLO' else 'SimboliEst'
                        feat = QgsFeature(layers[target].fields())
                        feat.setGeometry(QgsGeometry.fromPointXY(
                            self._tp(lines[i + 3], lines[i + 4], cs_transform)))
                        feat.setAttributes(base_attrs + ['SIMBOLI', int(lines[i + 1]), float(lines[i + 2])])
                        layers[target].dataProvider().addFeature(feat)
                    except Exception:
                        pass
                    i += 5

                elif line in ['FIDUCIALE', 'FIDUCIALE\\']:
                    try:
                        target = 'Fiduciali' if line == 'FIDUCIALE' else 'FiducialiEst'
                        feat = QgsFeature(layers[target].fields())
                        feat.setGeometry(QgsGeometry.fromPointXY(
                            self._tp(lines[i + 3], lines[i + 4], cs_transform)))
                        feat.setAttributes(base_attrs + [lines[i + 1], int(lines[i + 2])])
                        layers[target].dataProvider().addFeature(feat)
                    except Exception:
                        pass
                    i += 7

                elif line in ['LINEA', 'LINEA\\']:
                    try:
                        target = 'LnVestizione' if line == 'LINEA' else 'LnVestEst'
                        pts = []
                        ptr = i + 3
                        for j in range(int(lines[i + 2])):
                            pts.append(self._tp(lines[ptr], lines[ptr + 1], cs_transform))
                            ptr += 2
                        feat = QgsFeature(layers[target].fields())
                        feat.setGeometry(QgsGeometry.fromPolylineXY(pts))
                        feat.setAttributes(base_attrs + ['VESTIZIONE', int(lines[i + 1])])
                        layers[target].dataProvider().addFeature(feat)
                        i = ptr
                    except Exception:
                        i += 1

                elif line == 'EOF':
                    break
                else:
                    i += 1

            # Aggiorna progress in base ai file
            self.progress.emit(int(30 + (idx + 1) / total * 40))

    def run(self):
        """Esegue la conversione nel thread separato."""
        try:
            # Prepara cartella output
            shape_dir = self.output_dir
            os.makedirs(shape_dir, exist_ok=True)

            # Cerca file CXF
            cxf_files = [f for f in os.listdir(self.folder_path) if f.lower().endswith('.cxf')]
            if not cxf_files:
                self.finished.emit(False, "Nessun file CXF trovato nella cartella selezionata.")
                return

            self.log.emit(f"Trovati {len(cxf_files)} file CXF.")
            if self.use_cassini:
                self.log.emit(f"Modalità: Cassini-Soldner → {self.crs_authid}")
            else:
                # Autodetect Roma 40 zone (EPSG:3003 / EPSG:3004) dal primo file
                first_cxf = os.path.join(self.folder_path, cxf_files[0])
                detected = _detect_roma40_crs(first_cxf)
                if detected:
                    if detected != self.crs_authid:
                        self.log.emit(
                            f"ℹ Autodetect CRS: rilevato {detected} "
                            f"(sovrascrive la selezione manuale {self.crs_authid})"
                        )
                    else:
                        self.log.emit(f"ℹ Autodetect CRS: confermato {detected}")
                    self.crs_authid = detected
                else:
                    self.log.emit(
                        f"ℹ Autodetect CRS: nessuna coordinata riconoscibile, "
                        f"uso {self.crs_authid}"
                    )
                self.log.emit(f"Sistema di riferimento: {self.crs_authid}")
            self.progress.emit(10)

            # Crea layer in memoria
            self.log.emit("Creazione layer in memoria...")
            layers = self._create_memory_layers()
            self.progress.emit(20)

            # Parsing
            self.log.emit("Avvio parsing file CXF...")
            self._parse_cxf_files(cxf_files, layers)
            self.log.emit("Parsing completato.")
            self.progress.emit(70)

            # Salvataggio layer
            ORDERED_KEYS = [
                'Fogli', 'Acque', 'Strade', 'Particelle', 'Fabbricati',
                'LnVestEst', 'LnVestizione',
                'SimboliEst', 'Simboli', 'FiducialiEst', 'Fiduciali',
                'TestiEst', 'Testi'
            ]

            dest_crs = QgsCoordinateReferenceSystem(self.crs_authid)
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.fileEncoding = "UTF-8"
            options.destCRS = dest_crs

            use_gpkg = (self.output_format == "GPKG")
            gpkg_path = os.path.join(shape_dir, "catasto.gpkg") if use_gpkg else None

            if use_gpkg and os.path.exists(gpkg_path):
                os.remove(gpkg_path)

            if use_gpkg:
                options.driverName = "GPKG"
            else:
                options.driverName = "ESRI Shapefile"

            saved_layers = {}
            total_keys = len(ORDERED_KEYS)
            gpkg_first_layer = True  # primo layer nel GPKG → crea il file

            for ki, name in enumerate(ORDERED_KEYS):
                if name in layers and layers[name].featureCount() > 0:
                    vl = layers[name]
                    qml_path = os.path.join(shape_dir, f"{name}.qml")

                    # Scrivi QML
                    if name in QML_STYLES and QML_STYLES[name].strip():
                        with open(qml_path, "w", encoding="utf-8") as qf:
                            qf.write(QML_STYLES[name])

                    if use_gpkg:
                        options.layerName = name
                        if gpkg_first_layer:
                            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
                            gpkg_first_layer = False
                        else:
                            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
                        QgsVectorFileWriter.writeAsVectorFormatV2(
                            vl, gpkg_path, QgsCoordinateTransformContext(), options
                        )
                        self.log.emit(f"  Aggiunto: {name} ({vl.featureCount()} feature) → catasto.gpkg")
                        saved_layers[name] = (f"{gpkg_path}|layername={name}", qml_path)
                    else:
                        shape_path = os.path.join(shape_dir, f"{name}.shp")
                        QgsVectorFileWriter.writeAsVectorFormatV2(
                            vl, shape_path, QgsCoordinateTransformContext(), options
                        )
                        self.log.emit(f"  Salvato: {name}.shp ({vl.featureCount()} feature)")
                        saved_layers[name] = (shape_path, qml_path)

                self.progress.emit(int(70 + (ki + 1) / total_keys * 20))

            # Carica in QGIS
            if self.load_in_qgis and saved_layers:
                self.log.emit("Caricamento layer in QGIS...")
                self._layers = (saved_layers, ORDERED_KEYS)

            self.progress.emit(100)
            self.log.emit("✔ Conversione completata con successo!")
            out_desc = gpkg_path if use_gpkg else shape_dir
            self.finished.emit(True, f"Conversione completata.\nFile salvati in:\n{out_desc}")

        except Exception as e:
            err = traceback.format_exc()
            self.log.emit(f"ERRORE: {str(e)}\n{err}")
            self.finished.emit(False, f"Errore durante la conversione:\n{str(e)}")


# =====================================================================
# DIALOG PRINCIPALE
# =====================================================================

class Cxf2ShpVestitoDialog(QDialog):
    """Finestra di dialogo principale del plugin CXF to Shape Vestito."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.thread = None
        self.setWindowTitle("CXF → Shapefile / GeoPackage Vestito  |  Catasto Italiano")
        self.setMinimumWidth(580)
        self._setup_ui()

    # ------------------------------------------------------------------
    # Costruzione interfaccia
    # ------------------------------------------------------------------

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(14, 14, 14, 14)

        # Titolo
        title = QLabel("Conversione CXF Catastale → Shapefile / GeoPackage")
        font = QFont()
        font.setPointSize(13)
        font.setBold(True)
        title.setFont(font)
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        main_layout.addWidget(title)

        subtitle = QLabel("Plugin per la conversione di file CXF (Agenzia delle Entrate) in Shapefile ESRI o GeoPackage con stili catastali")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size: 10px;")
        main_layout.addWidget(subtitle)

        # Separatore
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        main_layout.addWidget(sep)

        # ---- Gruppo: Cartella input ----
        grp_input = QGroupBox("Cartella sorgente (contenente i file .cxf)")
        grp_layout = QHBoxLayout(grp_input)
        self.txt_folder = QLineEdit()
        self.txt_folder.setPlaceholderText("Seleziona la cartella con i file CXF...")
        self.txt_folder.setReadOnly(True)
        btn_browse = QPushButton("Sfoglia…")
        btn_browse.setFixedWidth(90)
        btn_browse.clicked.connect(self._browse_folder)
        grp_layout.addWidget(self.txt_folder)
        grp_layout.addWidget(btn_browse)
        main_layout.addWidget(grp_input)

        # ---- Gruppo: Opzioni output ----
        grp_opt = QGroupBox("Opzioni di output")
        opt_layout = QGridLayout(grp_opt)

        opt_layout.addWidget(QLabel("Formato output:"), 0, 0)
        self.cmb_format = QComboBox()
        self.cmb_format.addItems(["ESRI Shapefile (.shp)", "GeoPackage (.gpkg)"])
        self.cmb_format.setToolTip(
            "Shapefile: un file .shp per layer\n"
            "GeoPackage: tutti i layer in un unico file catasto.gpkg"
        )
        opt_layout.addWidget(self.cmb_format, 0, 1)

        opt_layout.addWidget(QLabel("Cartella output:"), 1, 0)
        out_widget = QWidget()
        out_hbox = QHBoxLayout(out_widget)
        out_hbox.setContentsMargins(0, 0, 0, 0)
        self.txt_output_dir = QLineEdit()
        self.txt_output_dir.setReadOnly(True)
        self.txt_output_dir.setPlaceholderText("Default: cartella CXF/shape  (o .../catasto per GPKG)")
        btn_browse_out = QPushButton("Sfoglia…")
        btn_browse_out.setFixedWidth(90)
        btn_browse_out.clicked.connect(self._browse_output_folder)
        out_hbox.addWidget(self.txt_output_dir)
        out_hbox.addWidget(btn_browse_out)
        opt_layout.addWidget(out_widget, 1, 1)

        self.chk_load = QCheckBox("Carica i layer nel progetto QGIS corrente")
        self.chk_load.setChecked(True)
        opt_layout.addWidget(self.chk_load, 2, 0, 1, 2)

        self.chk_hide_ext = QCheckBox("Nascondi layer 'Est' (esterni alla mappa) dopo il caricamento")
        self.chk_hide_ext.setChecked(True)
        opt_layout.addWidget(self.chk_hide_ext, 3, 0, 1, 2)

        # ---- Opzioni avanzate: Cassini-Soldner ----
        grp_cs = QgsCollapsibleGroupBox("Cassini-Soldner (CXF catasto storico)")
        grp_cs.setCollapsed(True)
        grp_cs.collapsedStateChanged.connect(lambda: self.adjustSize())
        cs_layout = QGridLayout(grp_cs)

        self.chk_cassini = QCheckBox(
            "File CXF in Cassini-Soldner — trasforma automaticamente in CRS target"
        )
        self.chk_cassini.setToolTip(
            "Attiva se i file CXF contengono coordinate Cassini-Soldner (catasto storico).\n"
            "Le coordinate vengono trasformate usando la tabella delle origini comunali\n"
            "bundled nel plugin (Italia_CS_PRJ_srtext.csv).\n\n"
            "CXF storici: coordinate X/Y ≈ ±poche migliaia di metri (piccoli valori)\n"
            "CXF moderni: coordinate X ≈ 1.400.000–1.900.000 (Monte Mario)"
        )
        self.chk_cassini.toggled.connect(self._on_cassini_toggled)
        cs_layout.addWidget(self.chk_cassini, 0, 0, 1, 2)

        self.lbl_crs = QLabel("CRS target:")
        cs_layout.addWidget(self.lbl_crs, 1, 0)
        self.crs_widget = QgsProjectionSelectionWidget()
        self.crs_widget.setCrs(QgsCoordinateReferenceSystem("EPSG:3003"))
        self.crs_widget.setToolTip(
            "EPSG:3003 = Monte Mario / Italy zone 1 (nord Italia)\n"
            "EPSG:3004 = Monte Mario / Italy zone 2 (sud Italia)"
        )
        cs_layout.addWidget(self.crs_widget, 1, 1)

        opt_layout.addWidget(grp_cs, 4, 0, 1, 2)

        main_layout.addWidget(grp_opt)

        # ---- Riepilogo layer generati ----
        grp_info = QGroupBox("Layer generati")
        info_layout = QVBoxLayout(grp_info)
        info_lbl = QLabel(
            "Poligoni: Fogli · Acque · Strade · Particelle · Fabbricati\n"
            "Linee: LnVestizione · LnVestEst\n"
            "Punti: Simboli · SimboliEst · Fiduciali · FiducialiEst\n"
            "Testi: Testi · TestiEst\n"
            "Tutti i layer vengono inseriti nel gruppo  \"Catasto Vestito\"  con stili QML preimpostati."
        )
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet("font-size: 10px;")
        info_layout.addWidget(info_lbl)
        main_layout.addWidget(grp_info)

        # ---- Log ----
        grp_log = QGroupBox("Log di esecuzione")
        log_layout = QVBoxLayout(grp_log)
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(110)
        self.txt_log.setStyleSheet("QTextEdit { font-size: 10px; font-family: Consolas, monospace; background-color: #f8f8f8; color: #222222; }")
        log_layout.addWidget(self.txt_log)
        main_layout.addWidget(grp_log)

        # ---- Progress bar ----
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        main_layout.addWidget(self.progress_bar)

        # ---- Pulsanti ----
        btn_layout = QHBoxLayout()
        btn_help = QPushButton("?  Guida")
        btn_help.setFixedHeight(36)
        btn_help.setToolTip("Mostra la guida del plugin")
        btn_help.clicked.connect(self._show_help)
        btn_layout.addWidget(btn_help)

        btn_layout.addStretch()

        self.btn_run = QPushButton("▶  Avvia Conversione")
        self.btn_run.setFixedHeight(36)
        self.btn_run.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; font-weight: bold; "
            "border-radius: 4px; padding: 4px 16px; }"
            "QPushButton:hover { background-color: #388e3c; }"
            "QPushButton:disabled { background-color: #aaa; }"
        )
        self.btn_run.clicked.connect(self._run_conversion)
        btn_layout.addWidget(self.btn_run)

        self.btn_close = QPushButton("Chiudi")
        self.btn_close.setFixedHeight(36)
        self.btn_close.clicked.connect(self.close)
        btn_layout.addWidget(self.btn_close)

        main_layout.addLayout(btn_layout)

    # ------------------------------------------------------------------
    # Slot / Azioni
    # ------------------------------------------------------------------

    def _on_cassini_toggled(self, checked):
        if checked:
            self.lbl_crs.setText("CRS target (output):")
            self.crs_widget.setToolTip(
                "CRS in cui verranno salvati gli shapefile dopo la trasformazione da Cassini-Soldner.\n\n"
                "EPSG:4326 = WGS84 geografico\n"
                "EPSG:3003 = Monte Mario / Italy zone 1\n"
                "EPSG:3004 = Monte Mario / Italy zone 2\n"
                "EPSG:32632 = WGS84 / UTM zone 32N"
            )
        else:
            self.lbl_crs.setText("CRS sorgente del file CXF:")
            self.crs_widget.setToolTip(
                "EPSG:3003 = Monte Mario / Italy zone 1 (nord Italia)\n"
                "EPSG:3004 = Monte Mario / Italy zone 2 (sud Italia)"
            )

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Seleziona la cartella contenente i file CXF", ""
        )
        if folder:
            self.txt_folder.setText(folder)

    def _browse_output_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Seleziona la cartella di output", ""
        )
        if folder:
            self.txt_output_dir.setText(folder)

    def _log(self, msg):
        self.txt_log.append(msg)

    def _run_conversion(self):
        folder_path = self.txt_folder.text().strip()
        if not folder_path or not os.path.isdir(folder_path):
            QMessageBox.warning(self, "Attenzione", "Seleziona una cartella valida prima di procedere.")
            return

        output_format = "GPKG" if self.cmb_format.currentIndex() == 1 else "SHP"
        custom_out = self.txt_output_dir.text().strip()
        if custom_out:
            output_dir = custom_out
        else:
            default_subdir = "catasto" if output_format == "GPKG" else "shape"
            output_dir = os.path.join(folder_path, default_subdir)
        load_in_qgis = self.chk_load.isChecked()
        hide_ext = self.chk_hide_ext.isChecked()
        use_cassini = self.chk_cassini.isChecked()
        crs = self.crs_widget.crs()
        if not crs.isValid():
            QMessageBox.warning(self, "Attenzione", "Il sistema di riferimento selezionato non è valido.")
            return
        crs_authid = crs.authid()

        if use_cassini and not CS_LOOKUP:
            QMessageBox.warning(
                self, "Attenzione",
                "Tabella Cassini-Soldner non trovata.\n"
                f"Assicurarsi che il file sia presente nella cartella del plugin:\n{_CS_CSV}"
            )
            return

        # Reset UI
        self.txt_log.clear()
        self.progress_bar.setValue(0)
        self.btn_run.setEnabled(False)

        # Warning duplicati CS
        if use_cassini and CS_DUPLICATES:
            dup_list = ", ".join(f"{k}({v}x)" for k, v in sorted(CS_DUPLICATES.items()))
            self._log(f"⚠ WARN: CodCata duplicati nel CSV (usata ultima riga): {dup_list}")

        # Avvia thread
        self.thread = ConversionThread(folder_path, output_dir, output_format, load_in_qgis, hide_ext, crs_authid, use_cassini)
        self.thread.progress.connect(self.progress_bar.setValue)
        self.thread.log.connect(self._log)
        self.thread.finished.connect(self._on_finished)
        self.thread.start()

    def _on_finished(self, success, message):
        """Chiamato al termine del thread di conversione."""
        self.btn_run.setEnabled(True)

        if success and self.chk_load.isChecked() and self.thread and self.thread._layers:
            self._load_layers_in_qgis(*self.thread._layers)

        if success:
            QMessageBox.information(self, "Completato", message)
        else:
            QMessageBox.critical(self, "Errore", message)

    def _load_layers_in_qgis(self, saved_layers, ordered_keys):
        """Carica i layer nel progetto QGIS corrente nel gruppo 'Catasto Vestito'."""
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        group = root.findGroup("Catasto Vestito") or root.addGroup("Catasto Vestito")

        hide_ext = self.chk_hide_ext.isChecked()

        for name in ordered_keys:
            if name not in saved_layers:
                continue
            shape_path, qml_path = saved_layers[name]
            loaded_layer = QgsVectorLayer(shape_path, name, "ogr")
            if loaded_layer.isValid():
                if os.path.exists(qml_path):
                    loaded_layer.loadNamedStyle(qml_path)
                project.addMapLayer(loaded_layer, False)
                node_layer = group.insertLayer(0, loaded_layer)
                node_layer.setExpanded(False)
                if hide_ext and name.endswith("Est"):
                    node_layer.setItemVisibilityChecked(False)
                self._log(f"  Caricato in QGIS: {name}")

    def _show_help(self):
        """Apre la finestra di guida del plugin."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Guida — CXF to Shapefile Vestito")
        dlg.setMinimumSize(560, 520)
        layout = QVBoxLayout(dlg)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        help_path = os.path.join(os.path.dirname(__file__), "help.html")
        if os.path.isfile(help_path):
            with open(help_path, "r", encoding="utf-8") as f:
                browser.setHtml(f.read())
        else:
            browser.setPlainText(f"File guida non trovato:\n{help_path}")
        layout.addWidget(browser)

        btn_close = QPushButton("Chiudi")
        btn_close.clicked.connect(dlg.close)
        layout.addWidget(btn_close)

        dlg.exec()

    def closeEvent(self, event):
        """Interrompe il thread se la finestra viene chiusa durante la conversione."""
        if self.thread and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait()
        super().closeEvent(event)
