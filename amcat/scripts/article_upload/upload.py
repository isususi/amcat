###########################################################################
#          (C) Vrije Universiteit, Amsterdam (the Netherlands)            #
#                                                                         #
# This file is part of AmCAT - The Amsterdam Content Analysis Toolkit     #
#                                                                         #
# AmCAT is free software: you can redistribute it and/or modify it under  #
# the terms of the GNU Affero General Public License as published by the  #
# Free Software Foundation, either version 3 of the License, or (at your  #
# option) any later version.                                              #
#                                                                         #
# AmCAT is distributed in the hope that it will be useful, but WITHOUT    #
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or   #
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public     #
# License for more details.                                               #
#                                                                         #
# You should have received a copy of the GNU Affero General Public        #
# License along with AmCAT.  If not, see <http://www.gnu.org/licenses/>.  #
###########################################################################

"""
Base module for article upload scripts
"""
import datetime
import logging
import os.path


from django import forms
from django.forms.widgets import HiddenInput

from amcat.models import Article, Project, ArticleSet
from amcat.models.articleset import create_new_articleset
from amcat.scripts import script
from amcat.scripts.article_upload.fileupload import RawFileUploadForm

log = logging.getLogger(__name__)

ARTICLE_FIELDS = ("text", "title", "url", "date", "parent_hash")


class ParseError(Exception):
    pass


class ArticleError(ParseError):
    pass


class MissingValueError(ArticleError):
    def __init__(self, field, *args):
        super().__init__("Missing value for field: '{}'".format(field), *args)



class UploadForm(RawFileUploadForm):
    project = forms.ModelChoiceField(queryset=Project.objects.all())

    articlesets = forms.ModelMultipleChoiceField(
        queryset=ArticleSet.objects.all(), required=False,
        help_text="If you choose an existing articleset, the articles will be "
                  "appended to that set. If you leave this empty, a new articleset will be "
                  "created using either the name given below, or using the file name")

    articleset_name = forms.CharField(
        max_length=ArticleSet._meta.get_field_by_name('name')[0].max_length,
        required=False)

    def clean_articleset_name(self):
        """If articleset name not specified, use file base name instead"""
        if 'articlesets' in self.errors:
            # skip check if error in articlesets: cleaned_data['articlesets'] does not exist
            return
        if self.files.get('file') and not (
                    self.cleaned_data.get('articleset_name') or self.cleaned_data.get('articleset')):
            fn = os.path.basename(self.files['file'].name)
            return fn
        name = self.cleaned_data['articleset_name']
        if not bool(name) ^ bool(self.cleaned_data['articlesets']):
            raise forms.ValidationError("Please specify either articleset or articleset_name")
        return name

    @classmethod
    def get_empty(cls, project=None, post=None, files=None, **_options):
        f = cls(post, files) if post is not None else cls()
        if project:
            f.fields['project'].initial = project.id
            f.fields['project'].widget = HiddenInput()
            f.fields['articlesets'].queryset = ArticleSet.objects.filter(project=project)
        return f


class UploadScript(script.Script):
    """Base class for Upload Scripts, which are scraper scripts driven by the
    the script input.

    For legacy reasons, parse_document and split_text may be used instead of the standard
    get_units and scrape_unit.
    """

    options_form = UploadForm

    def __init__(self, *args, **kargs):
        super(UploadScript, self).__init__(*args, **kargs)
        self.project = self.options['project']
        self.errors = []

    @property
    def articlesets(self):
        if self.options['articlesets']:
            return self.options['articlesets']

        if self.options['articleset_name']:
            aset = create_new_articleset(self.options['articleset_name'], self.project)
            self.options['articlesets'] = (aset,)
            return (aset,)

        return ()

    def explain_error(self, error, article=None):
        """Explain the error in the context of unit for the end user"""
        index = " {}".format(article) if article is not None else ""
        return "Error in article or filepart{}: {}.".format(index, error)

    def decode(self, bytes):
        """Decode the bytes using the encoding from the form"""
        enc, text = self.bound_form.decode(bytes)
        return text

    @property
    def uploaded_texts(self):
        """A cached sequence of UploadedFile objects"""
        try:
            return self._input_texts
        except AttributeError:
            self._input_texts = self.bound_form.get_uploaded_texts()
            return self._input_texts

    def get_provenance(self, file, articles):
        n = len(articles)
        filename = file and file.name
        timestamp = str(datetime.datetime.now())[:16]
        return ("[{timestamp}] Uploaded {n} articles from file {filename!r} "
                "using {self.__class__.__name__}".format(**locals()))

    def parse_file(self, file):
        for i, unit in enumerate(self._get_units(file), 1):
            try:
                for a in self._scrape_unit(unit):
                    yield a
            except ParseError as e:
                raise ParseError("{}".format(self.explain_error(e, article=i)))

    def run(self, _dummy=None):
        monitor = self.progress_monitor

        file = self.options['file']
        filename = file and file.name
        monitor.update(10, u"Importing {self.__class__.__name__} from {filename} into {self.project}"
                       .format(**locals()))

        articles_errors = []

        files = list(self._get_files())
        nfiles = len(files)
        for i, f in enumerate(files):
            filename = getattr(f, 'name', str(f))
            monitor.update(20 / nfiles, "Parsing file {i}/{nfiles}: {filename}".format(**locals()))
            articles_errors += [(x, None) if isinstance(x, Article) else (None, x) for x in self.parse_file(f)]

        article_error = False
        for article, error in articles_errors:
            if error:
                article_error = True
            if isinstance(article, Article):
                _set_project(article, self.project)

        articles, errors = map(list, zip(*articles_errors))

        if self.errors or article_error:
            article_errors = [errors for error in errors if error]
            raise ParseError(self.errors, article_errors)

        monitor.update(10, "All files parsed, saving {n} articles".format(n=len(articles)))
        Article.create_articles(articles, articlesets=self.articlesets,
                                monitor=monitor.submonitor(40))

        if not articles:
            raise Exception("No articles were imported")

        monitor.update(10, "Uploaded {n} articles, post-processing".format(n=len(articles)))
        self.postprocess(articles)

        for aset in self.articlesets:
            new_provenance = self.get_provenance(file, articles)
            aset.provenance = ("%s\n%s" % (aset.provenance or "", new_provenance)).strip()
            aset.save()

        if getattr(self, 'task', None):
            self.task.log_usage("articles", "upload", n=len(articles))

        monitor.update(10, "Done! Uploaded articles".format(n=len(articles)))
        return [a.id for a in self.articlesets]

    def postprocess(self, articles):
        """
        Optional postprocessing of articles. Removing aricles from the list will exclude them from the
        articleset (if needed, list should be changed in place)
        """
        pass

    def _get_files(self):
        return self.bound_form.get_entries()

    def _get_units(self, file):
        return self.split_file(file)

    def _scrape_unit(self, document):
        result = self.parse_document(document)
        if isinstance(result, Article):
            result = [result]
        for art in result:
            if isinstance(art, dict):
                try:
                    yield self.article_from_dict(art)
                except ArticleError as e:
                    yield e
            else:
                yield art

    def article_from_dict(self, parse_dict):
        mapped_fields = {}
        if 'field_map' in self.options:
            for field_to, field_from in self.options['field_map'].items():
                value = None
                if 'column' in field_from:
                    value = parse_dict.get(field_from['column'])
                if value is None and (field_from.get('use_default') or 'column' not in field_from):
                    try:
                        value = field_from['value']
                    except KeyError:
                        raise MissingValueError(field_to)
                mapped_fields[field_to] = value
        article_kwargs = {}

        for article_field in ARTICLE_FIELDS:
            article_kwargs[article_field] = mapped_fields.pop(article_field, None)

        for article_field in get_required():
            if article_kwargs[article_field] is None:
                raise MissingValueError(article_field)

        article_kwargs['properties'] = mapped_fields

        return Article(**article_kwargs)

    def parse_document(self, document):
        """
        Parse the document as one or more articles, provided for legacy purposes

        @param document: object received from split_text, e.g. a string fragment
        @return: None, an Article or a sequence of Article(s)
        """
        raise NotImplementedError()

    def split_file(self, file):
        """
        Split the file into one or more fragments representing individual documents.
        Default implementation returns a single fragment containing the unicode text.

        @type file: file like object
        @return: a sequence of objects (e.g. strings) to pass to parse_documents
        """
        return [file]


def _set_project(art, project):
    try:
        if getattr(art, "project", None) is not None: return
    except Project.DoesNotExist:
        pass  # django throws DNE on x.y if y is not set and not nullable
    art.project = project


_required = set()


def get_required():
    if not _required:
        for field in ARTICLE_FIELDS:
            if not Article._meta.get_field(field).blank:
                _required.add(field)
    return _required
