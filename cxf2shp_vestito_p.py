# -*- coding: utf-8 -*-
"""
cxf2shp_vestito_p.py
Classe principale del plugin CXF to Shape Vestito.
Autore: Fortunato Amore
"""

import os
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import QCoreApplication


class Cxf2ShpVestito:
    """Classe principale del plugin CXF to Shape Vestito."""

    def __init__(self, iface):
        """Inizializza il plugin.

        Args:
            iface: istanza dell'interfaccia QGIS
        """
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.menu = self.tr('&Catasto CXF')
        self.toolbar = self.iface.addToolBar('Cxf2ShpVestitoToolbar')
        self.toolbar.setObjectName('Cxf2ShpVestitoToolbar')
        self._dialog = None

    def tr(self, message):
        """Traduce una stringa usando Qt."""
        return QCoreApplication.translate('Cxf2ShpVestito', message)

    def add_action(self, icon_path, text, callback,
                   enabled=True, add_to_menu=True,
                   add_to_toolbar=True, parent=None,
                   status_tip=None, whats_this=None):
        """Aggiunge icona nella toolbar e voce nel menu."""
        icon = QIcon(icon_path)
        action = QAction(icon, text, parent or self.iface.mainWindow())
        action.triggered.connect(callback)
        action.setEnabled(enabled)

        if status_tip:
            action.setStatusTip(status_tip)
        if whats_this:
            action.setWhatsThis(whats_this)
        if add_to_toolbar:
            self.toolbar.addAction(action)
        if add_to_menu:
            self.iface.addPluginToVectorMenu(self.menu, action)
            self.iface.addPluginToMenu(self.menu, action)

        self.actions.append(action)
        return action

    def initGui(self):
        """Crea le voci di menu e le icone nella toolbar."""
        icon_path = os.path.join(self.plugin_dir, 'icon.png')
        self.add_action(
            icon_path,
            text=self.tr('Converti CXF in Shapefile (Vestito)'),
            callback=self.run,
            status_tip=self.tr('Converte file CXF catastali in Shapefile con stili QML'),
            whats_this=self.tr('Apre la finestra di dialogo per convertire file CXF in Shapefile'),
            parent=self.iface.mainWindow()
        )

    def unload(self):
        """Rimuove le voci di menu e le icone dalla toolbar."""
        for action in self.actions:
            self.iface.removePluginVectorMenu(self.menu, action)
            self.iface.removePluginMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)
        del self.toolbar

    def run(self):
        """Avvia il plugin: apre la finestra di dialogo principale."""
        from .cxf2shp_vestito_d import Cxf2ShpVestitoDialog
        dlg = Cxf2ShpVestitoDialog(self.iface, self.iface.mainWindow())
        dlg.exec()
