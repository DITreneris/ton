"""Paper-mode GRAM <-> issuer-verified USD₮ worker. No signing. No broadcast."""

__version__ = "1.0.0"
MODE = "PAPER"

from ton_gram.ledger import Ledger as _Ledger
from ton_gram.ledger_ext import install as _install_ledger

_install_ledger(_Ledger)
