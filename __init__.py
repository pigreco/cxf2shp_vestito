# -*- coding: utf-8 -*-
"""
CXF to Shape Vestito
Converte file CXF catastali italiani in Shapefile con stili QML preimpostati.
Autore: Fortunato Amore
"""


def classFactory(iface):
    """Carica la classe principale del plugin.

    Args:
        iface: istanza dell'interfaccia QGIS
    """
    from .cxf2shp_vestito_p import Cxf2ShpVestito
    return Cxf2ShpVestito(iface)
