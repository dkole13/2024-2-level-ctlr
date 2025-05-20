"""
Pipeline for CONLL-U formatting.
"""

# pylint: disable=too-few-public-methods, undefined-variable, too-many-nested-blocks
import pathlib

import spacy_udpipe
from networkx import DiGraph
from spacy_conll.parser import ConllParser

from core_utils.article.article import Article, ArtifactType, get_article_id_from_filepath
from core_utils.article.io import from_meta, from_raw, to_cleaned, to_meta
from core_utils.constants import ASSETS_PATH
from core_utils.pipeline import (
    AbstractCoNLLUAnalyzer,
    CoNLLUDocument,
    LibraryWrapper,
    PipelineProtocol,
    StanzaDocument,
    TreeNode,
    UDPipeDocument,
    UnifiedCoNLLUDocument,
)
from core_utils.visualizer import visualize


class EmptyDirectoryError(Exception):
    """
    Raised when dataset directory is empty.
    """


class InconsistentDatasetError(Exception):
    """
    Raised when the dataset is inconsistent: IDs contain slips, number of meta and raw files is not equal, files are empty.
    """


class EmptyFileError(Exception):
    """
    Raised when an article file is empty.
    """


class CorpusManager:
    """
    Work with articles and store them.
    """

    def __init__(self, path_to_raw_txt_data: pathlib.Path) -> None:
        """
        Initialize an instance of the CorpusManager class.

        Args:
            path_to_raw_txt_data (pathlib.Path): Path to raw txt data
        """
        self.path = path_to_raw_txt_data
        self._storage = {}
        self._validate_dataset()
        self._scan_dataset()

    def _validate_dataset(self) -> None:
        """
        Validate folder with assets.
        """
        if not self.path.exists():
            raise FileNotFoundError
        if not self.path.is_dir():
            raise NotADirectoryError
        if not any(self.path.iterdir()):
            raise EmptyDirectoryError
        meta, raw = [], []
        for filepath in self.path.iterdir():
            if filepath.name.endswith('_meta.json'):
                meta.append(filepath.name)
            elif filepath.name.endswith('_raw.txt'):
                raw.append(filepath.name)
        if len(meta) != len(raw):
            raise InconsistentDatasetError(
                f'The amounts of meta and raw files are not equal: {len(meta)} != {len(raw)}.')
        meta_template = [f'{count}_meta.json' for count in range(1, len(meta) + 1)]
        if set(meta) != set(meta_template):
            raise InconsistentDatasetError('IDs of meta files are inconsistent.')
        raw_template = [f'{count}_raw.txt' for count in range(1, len(raw) + 1)]
        if set(raw) != set(raw_template):
            raise InconsistentDatasetError('IDs of raw files are inconsistent.')
        for filepath in self.path.iterdir():
            if filepath.stat().st_size == 0:
                raise InconsistentDatasetError(f'File {filepath} is empty.')

    def _scan_dataset(self) -> None:
        """
        Register each dataset entry.
        """
        self._storage = {get_article_id_from_filepath(filepath): from_raw(filepath)
                         for filepath in self.path.glob('*_raw.txt')}

    def get_articles(self) -> dict:
        """
        Get storage params.

        Returns:
            dict: Storage params
        """
        return self._storage


class TextProcessingPipeline(PipelineProtocol):
    """
    Preprocess and morphologically annotate sentences into the CONLL-U format.
    """

    def __init__(
            self, corpus_manager: CorpusManager, analyzer: LibraryWrapper | None = None
    ) -> None:
        """
        Initialize an instance of the TextProcessingPipeline class.

        Args:
            corpus_manager (CorpusManager): CorpusManager instance
            analyzer (LibraryWrapper | None): Analyzer instance
        """
        self.corpus_manager = corpus_manager
        self._analyzer = analyzer

    def run(self) -> None:
        """
        Perform basic preprocessing and write processed text to files.
        """
        conllu = self._analyzer.analyze([article.text for article
                                         in self.corpus_manager.get_articles().values()])
        for idx, article in enumerate(self.corpus_manager.get_articles().values()):
            article.text = article.text.replace('NBSP', '')
            to_cleaned(article)
            article.set_conllu_info(conllu[idx])
            self._analyzer.to_conllu(article)


class UDPipeAnalyzer(LibraryWrapper):
    """
    Wrapper for udpipe library.
    """

    #: Analyzer
    _analyzer: AbstractCoNLLUAnalyzer

    def __init__(self) -> None:
        """
        Initialize an instance of the UDPipeAnalyzer class.
        """
        self._analyzer = self._bootstrap()

    def _bootstrap(self) -> AbstractCoNLLUAnalyzer:
        """
        Load and set up the UDPipe model.

        Returns:
            AbstractCoNLLUAnalyzer: Analyzer instance
        """
        if not pathlib.Path(model_path := 'lab_6_pipeline/assets/model/russian-syntagrus-ud-2.0'
                                          '-170801.udpipe').exists():
            raise FileNotFoundError("Path to model does not exists or is invalid")
        model = spacy_udpipe.load_from_path(
            lang='ru',
            path=model_path
        )
        model.add_pipe(
            "conll_formatter",
            last=True,
            config={"conversion_maps": {"XPOS": {"": "_"}}, "include_headers": True},
        )
        return model

    def analyze(self, texts: list[str]) -> list[UDPipeDocument | str]:
        """
        Process texts into CoNLL-U formatted markup.

        Args:
            texts (list[str]): Collection of texts

        Returns:
            list[UDPipeDocument | str]: List of documents
        """
        return [f'{self._analyzer(text)._.conll_str}\n' for text in texts]

    def to_conllu(self, article: Article) -> None:
        """
        Save content to ConLLU format.

        Args:
            article (Article): Article containing information to save
        """
        path = article.get_file_path(ArtifactType.UDPIPE_CONLLU)
        with open(path, 'w', encoding='utf-8') as file:
            file.write(article.get_conllu_info())

    def from_conllu(self, article: Article) -> UDPipeDocument:
        """
        Load ConLLU content from article stored on disk.

        Args:
            article (Article): Article to load

        Returns:
            UDPipeDocument: Document ready for parsing
        """
        conllu_path = article.get_file_path(ArtifactType.UDPIPE_CONLLU)
        if pathlib.Path(conllu_path).stat().st_size == 0:
            raise EmptyFileError
        return ConllParser(self._analyzer).parse_file_as_conll(input_file=conllu_path)

    def get_document(self, doc: UDPipeDocument) -> UnifiedCoNLLUDocument:
        """
        Present ConLLU document's sentence tokens as a unified structure.

        Args:
            doc (UDPipeDocument): ConLLU document

        Returns:
            UnifiedCoNLLUDocument: Dictionary of token features within document sentences
        """


class StanzaAnalyzer(LibraryWrapper):
    """
    Wrapper for stanza library.
    """

    #: Analyzer
    _analyzer: AbstractCoNLLUAnalyzer

    def __init__(self) -> None:
        """
        Initialize an instance of the StanzaAnalyzer class.
        """

    def _bootstrap(self) -> AbstractCoNLLUAnalyzer:
        """
        Load and set up the Stanza model.

        Returns:
            AbstractCoNLLUAnalyzer: Analyzer instance
        """

    def analyze(self, texts: list[str]) -> list[StanzaDocument]:
        """
        Process texts into CoNLL-U formatted markup.

        Args:
            texts (list[str]): Collection of texts

        Returns:
            list[StanzaDocument]: List of documents
        """

    def to_conllu(self, article: Article) -> None:
        """
        Save content to ConLLU format.

        Args:
            article (Article): Article containing information to save
        """

    def from_conllu(self, article: Article) -> StanzaDocument:
        """
        Load ConLLU content from article stored on disk.

        Args:
            article (Article): Article to load

        Returns:
            StanzaDocument: Document ready for parsing
        """

    def get_document(self, doc: StanzaDocument) -> UnifiedCoNLLUDocument:
        """
        Present ConLLU document's sentence tokens as a unified structure.

        Args:
            doc (StanzaDocument): ConLLU document

        Returns:
            UnifiedCoNLLUDocument: Document of token features within document sentences
        """


class POSFrequencyPipeline:
    """
    Count frequencies of each POS in articles, update meta info and produce graphic report.
    """

    def __init__(self, corpus_manager: CorpusManager, analyzer: LibraryWrapper) -> None:
        """
        Initialize an instance of the POSFrequencyPipeline class.

        Args:
            corpus_manager (CorpusManager): CorpusManager instance
            analyzer (LibraryWrapper): Analyzer instance
        """
        self._corpus = corpus_manager
        self._analyzer = analyzer

    def _count_frequencies(self, article: Article) -> dict[str, int]:
        """
        Count POS frequency in Article.

        Args:
            article (Article): Article instance

        Returns:
            dict[str, int]: POS frequencies
        """
        article_conllu = self._analyzer.from_conllu(article)
        pos_frequencies = {}
        for sentence in article_conllu.sentences:
            upos = [word.todict().get('upos') for word in sentence.words]
            pos_frequencies = {pos: upos.count(pos) for pos in upos}
        return pos_frequencies

    def run(self) -> None:
        """
        Visualize the frequencies of each part of speech.
        """
        for idx, article in self._corpus.get_articles().items():
            from_meta(article.get_meta_file_path(), article)
            article.set_pos_info(self._count_frequencies(article))
            to_meta(article)
            visualize(article, pathlib.Path(ASSETS_PATH) / f"{idx}_image.png")


class PatternSearchPipeline(PipelineProtocol):
    """
    Search for the required syntactic pattern.
    """

    def __init__(
            self, corpus_manager: CorpusManager, analyzer: LibraryWrapper, pos: tuple[str, ...]
    ) -> None:
        """
        Initialize an instance of the PatternSearchPipeline class.

        Args:
            corpus_manager (CorpusManager): CorpusManager instance
            analyzer (LibraryWrapper): Analyzer instance
            pos (tuple[str, ...]): Root, Dependency, Child part of speech
        """

    def _make_graphs(self, doc: CoNLLUDocument) -> list[DiGraph]:
        """
        Make graphs for a document.

        Args:
            doc (CoNLLUDocument): Document for patterns searching

        Returns:
            list[DiGraph]: Graphs for the sentences in the document
        """

    def _add_children(
            self, graph: DiGraph, subgraph_to_graph: dict, node_id: int, tree_node: TreeNode
    ) -> None:
        """
        Add children to TreeNode.

        Args:
            graph (DiGraph): Sentence graph to search for a pattern
            subgraph_to_graph (dict): Matched subgraph
            node_id (int): ID of root node of the match
            tree_node (TreeNode): Root node of the match
        """

    def _find_pattern(self, doc_graphs: list) -> dict[int, list[TreeNode]]:
        """
        Search for the required pattern.

        Args:
            doc_graphs (list): A list of graphs for the document

        Returns:
            dict[int, list[TreeNode]]: A dictionary with pattern matches
        """

    def run(self) -> None:
        """
        Search for a pattern in documents and writes found information to JSON file.
        """


def main() -> None:
    """
    Entrypoint for pipeline module.
    """
    path = pathlib.Path(__file__).parent.parent / "tmp" / "articles"
    corpus_manager = CorpusManager(path)
    udpipe_analyzer = UDPipeAnalyzer()


if __name__ == "__main__":
    main()
