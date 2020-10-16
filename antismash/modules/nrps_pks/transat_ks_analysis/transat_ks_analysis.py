# License: GNU Affero General Public License v3 or later
# A copy of GNU AGPL v3 should have been included in this software package in LICENSE.txt.

""" transPACT analysis of KS domains """

from typing import Any, Dict, List, Tuple

from jinja2 import Markup

from antismash.common import path, subprocessing, utils, fasta
from antismash.modules.nrps_pks.data_structures import Prediction
from antismash.modules.nrps_pks.pks_names import get_long_form

from Bio import Phylo
from io import StringIO
import re, copy

_LEAF2CLADE_TBL = path.get_full_path(__file__, "data", "transPACT_leaf2clade.tsv")
_PPLACER_MASS_CUTOFF = 0.6 ## transPACT default: 0.6; higher = more stringent
_PPLACER_REFERENCE_PKG = path.get_full_path(__file__, "data", "RAxML_bestTree.649KS_sequences_hmmalign_raxml_renamed.refpkg") ## Note: Reference package creation: taxit create --aln-fasta reference.fasta --tree-stats reference.log --tree-file reference.nwk -P reference.refpkg
_KS_REFERENCE_ALIGNMENT = '/'.join([_PPLACER_REFERENCE_PKG, '649KS_sequences_031218.fasta'])
_KS_REFERENCE_TREE = '/'.join([_PPLACER_REFERENCE_PKG, 'RAxML_bestTree.649KS_sequences_hmmalign_raxml_renamed.tre'])

class KSResult:
    """ A result for a specific KS domain """
    __slots__ = ["clade", "specificity"]

    def __init__(self, clade: str, specificity: str) -> None:
        assert isinstance(clade, str)
        assert isinstance(specificity, str)
        self.clade = clade
        self.specificity = specificity

    def __repr__(self) -> str:
        return str(self)

    def __str__(self) -> str:
        return "KSResult(clade=%s, specificity=%s)" % (self.clade, self.specificity)

    def to_json(self) -> Tuple[str, str]:
        """ Serialises the instance """
        return (self.clade, self.specificity)

    @staticmethod
    def from_json(json: Tuple[str, str]) -> "KSResult":
        """ Deserialise an KSResult instance """
        assert len(json) == 2
        return KSResult(*json)


class KSPrediction(Prediction):
    """ Holds the transPACT predictions for a domain"""
    def __init__(self, predictions: Dict[str, KSResult]) -> None:
        super().__init__("transPACT_KS")
        self.predictions = predictions

    def get_classification(self) -> List[str]:
        results = []  # type: List[str]
        if not self.predictions:
            return results
        for clade, pred in self.predictions:
            results.append(clade)
        return results
        
    def as_html(self) -> Markup:
        if not self.predictions:
            return Markup("No matches")
        lines = []
        for clade, pred in self.predictions[:2]:
            lines.append("<dd>%s: %.1f%%</dd>\n" % (clade, pred.specificity))
        html = ((
            "<dl>\n"
            " <dt>transPACT assigned specificiy:</dt>\n"
            "%s"
            "</dl>\n"
        ) % "".join(lines))
        return Markup(html)

    def to_json(self) -> Dict[str, Any]:
        return {
            "method": "transPACT_KS",
            "predictions": {clade: pred.to_json() for clade, pred in self.predictions},
        }

    @staticmethod
    def from_json(json: Dict[str, Any]) -> "KSPrediction":
        assert json["method"] == "transPACT_KS"
        return KSPrediction(json["prediction"])
    
def get_leaf2clade(leaf2cladetbl: str) -> [Dict[str, str], Dict[str, str]]:
    leaf2clade = {}
    clade2ann = {}
    with open(leaf2cladetbl) as c:
        for ln in c.read().splitlines():
            ksname, clade, ann = ln.split("\t")
            leaf2clade[ksname] = clade
            clade2ann[clade] = ann.replace(' ','_')
    return(leaf2clade, clade2ann)


def get_transpact_clade(query_name: str, tree, funClades: Dict[str, str]) -> str:
    """
    tree: Bio.Phylo.Newick.Tree
    """
    ## Remove placements that aren't the query in question and note query's elders
    newtree = copy.deepcopy(tree) ## necessary to not screw up original tree for subsequent queries
    parent, grandparent = None, None
    for leaf in newtree.get_terminals():
        if leaf.name == query_name:
            node_path = newtree.get_path(leaf)
            parent, grandparent = node_path[-2], node_path[-3]
        else:
            ln = leaf.name.split("_")
            if re.match("^#\d+$", ln[-2]) is not None:
                newtree.prune(leaf)
    ## Find the proper clade elder, if more than two siblings use parent, otherwise use grandparent
    elder = None
    if len(parent.get_terminals()) > 2:
        elder = parent
    else:
        elder = grandparent
    ## Count number of occurances of each clade in elder descendants
    clade_count = {} # Dict[str, int]
    for leaf in elder.get_terminals():
        if leaf.name != query_name:
            if funClades[leaf.name] in clade_count:
                clade_count[funClades[leaf.name]] += 1
            else:
                clade_count[funClades[leaf.name]] = 1
    clade_assignment = 'clade_not_conserved' ## Check with simon...maybe None?
    if len(clade_count) == 1: ## clade consensus, monophyly
        clade_assignment = list(clade_count)[0]
    return clade_assignment

    
def transpact_tree_prediction(pplacer_tree: str, masscutoff: float, funClades: Dict[str, str], clade2ann: Dict[str, str]) -> KSPrediction:

    t = Phylo.read(StringIO(pplacer_tree), 'newick')
    tree_hits = {}
    for leaf in t.get_terminals():
        ln = leaf.name.split("_") ## Note: have to use leaf.name here, if use str(leaf) names are truncated if over 40 char
        if re.match("^#\d+$", ln[-2]) is not None: ## Fits pplacer format
            n = re.sub(r"^#(\d+)$", "\g<1>", ln[-2]) ## placement number, zero indexed
            tree_hits[n] = leaf
            funClades[leaf.name] = 'query_seq'
    ## Look to see when threshold is met
    totalmass = {}
    query_prefix = None
    for placement_num in tree_hits:
        if query_prefix is None:
            query_prefix = re.sub(r"^(.+)_#\d+_M=\d+?\.?\d*$", "\g<1>", tree_hits[placement_num].name)
        mass = float(re.sub(r"^.+#\d+_M=(\d+?\.?\d*)$", "\g<1>", tree_hits[placement_num].name))
        clade_assignment = get_transpact_clade(tree_hits[placement_num].name, t, funClades)
        if clade_assignment in totalmass:
            totalmass[clade_assignment] += mass
        else:
            totalmass[clade_assignment] = mass
    best_clade = max(totalmass, key=totalmass.get)
    clade, spec = 'clade_not_conserved', 'NA' ## Talk to Simon about best way to treat non-predictions...maybe 'None'?
    if totalmass[best_clade] >= masscutoff:
        clade, spec = best_clade, clade2ann[best_clade]
    return KSPrediction({spec: KSResult(clade, spec)})

    
def run_transpact_pplacer(ks_name: str, alignment: Dict[str, str], reference_pkg: str, reference_aln: str, reference_tree: str, masscutoff: float, funClades: Dict[str, str], clade2ann: Dict[str, str]) -> KSPrediction:
    
    pplacer_tree = subprocessing.run_pplacer(ks_name, alignment, reference_pkg, reference_aln, reference_tree)
    prediction = transpact_tree_prediction(pplacer_tree, masscutoff, funClades, clade2ann)
    return prediction
    
    
def run_transpact_ks_analysis(domains: Dict[str, str]) -> Dict[str, Prediction]:
    """ Analyses PKS signature of KS domains

        Arguments:
            domains: a dictionary mapping domain identifier (e.g. 'locus_KS2')
                     to domain sequence

        Returns:
            a dictionary mapping domain identifier to
                a list of KSResults
    """
    ## Read clade to annotation maps from flat files
    funClades, clade2ann = get_leaf2clade(_LEAF2CLADE_TBL)

    results = {}
    for ks_name, ks_seq in domains.items():
        ## Align to reference
        alignment = subprocessing.run_muscle_single(ks_name, ks_seq, _KS_REFERENCE_ALIGNMENT)
        results[ks_name] = run_transpact_pplacer(ks_name, alignment, _PPLACER_REFERENCE_PKG, _KS_REFERENCE_ALIGNMENT, _KS_REFERENCE_TREE, _PPLACER_MASS_CUTOFF, funClades, clade2ann)
    return results
