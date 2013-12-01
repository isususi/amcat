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
import json
from amcat.models import Medium, Article
from api.rest.serializer import AmCATModelSerializer

__all__ = ("ArticleSerializer",)

class ArticleSerializer(AmCATModelSerializer):

    def __init__(self, instance=None, data=None, files=None, **kwargs):
        kwargs['many'] = isinstance(data, list)
        super(ArticleSerializer, self).__init__(instance, data, files, **kwargs)

    def restore_fields(self, data, files):
        # convert media from name to id, if needed
        data = data.copy() # make data mutable
        if 'medium' in data:
            try:
                int(data['medium'])
            except ValueError:
                if not hasattr(self, 'media'):
                    self.media = {}
                m = data['medium']
                if m not in self.media:
                    self.media[m] = Medium.get_or_create(m).id
                data['medium'] = self.media[m]

        # add time part to date, if needed
        if 'date' in data and len(data['date']) == 10:
            data['date'] += "T00:00"

        if 'project' not in data:
            data['project'] = self.context['view'].project.id

        return super(ArticleSerializer, self).restore_fields(data, files)

    def from_native(self, data, files):
        result = super(ArticleSerializer, self).from_native(data, files)

        # deserialize children (if needed)
        children = data.get('children')# TODO: children can be a multi-value GET param as well, e.g. handle getlist

        if isinstance(children, (str, unicode)):
            children = json.loads(children)

        if children:
            self.many = True
            def get_child(obj):
                child = self.from_native(obj, None)
                child.parent = result
                return child
            return [result] + [get_child(child) for child in children]

        return result

    def save(self, **kwargs):
        import collections
        def _flatten(l):
            """Turn either an object or a (recursive/irregular/jagged) list-of-lists into a flat list"""
            # inspired by http://stackoverflow.com/questions/2158395/flatten-an-irregular-list-of-lists-in-python
            if isinstance(l, collections.Iterable) and not isinstance(l, basestring):
                for el in l:
                    for sub in _flatten(el):
                        yield sub
            else:
                yield l

        # flatten articles list (children in a many call yields a list of lists)
        self.object = list(_flatten(self.object))

        Article.create_articles(self.object, self.context['view'].articleset)

        # make sure that self.many is True for serializing result
        self.many = True
        return self.object