import os
import urllib
import cgi
import time
import logging

from google.appengine.api import urlfetch
from google.appengine.api import images
from google.appengine.api import users
from google.appengine.api import mail
#from google.appengine.ext import webapp
import webapp2 as webapp
from google.appengine.ext import db
from jinja2 import FileSystemLoader, Environment
from google.appengine.ext import blobstore
from google.appengine.ext.webapp import blobstore_handlers
from django.utils import simplejson as json

import html2text

CATEGORIES = ["0-20 euro", "21-50 euro", "51-100 euro", "Oltre 100 euro", "Altro"]

env = Environment(loader=FileSystemLoader("./"))

class Item(db.Model):
    title = db.StringProperty()
    info = db.StringProperty(multiline=True, indexed=False)
    image = db.BlobProperty()
    category = db.StringProperty()
    position = db.IntegerProperty()
    tot_parts = db.IntegerProperty()
    avail_parts = db.IntegerProperty()
    part_price = db.FloatProperty()
    tot_price = db.FloatProperty()

class Gift(db.Model):
    email = db.StringProperty()
    sender = db.StringProperty()
    date = db.DateTimeProperty(auto_now_add=True)
    message = db.TextProperty()
    code = db.StringProperty()
    public = db.BooleanProperty()
    cart = db.TextProperty() # [{key, name, quantity, price}, ]
    total = db.FloatProperty()
    confirmed = db.BooleanProperty(default=False)

class MainPage(webapp.RequestHandler):
    def post(self):
        groom = self.request.get('groom').strip().lower().split()
        bride = self.request.get('bride').strip().lower().split()
        if 'matteo' in groom and 'elisa' in bride:
            self.response.set_cookie('groombride', 'matteoelisa', max_age=60*60*24*7)
        self.redirect('/')
    def _check_login(self):
        if (self.request.cookies.get('groombride', None) == 'matteoelisa') or users.is_current_user_admin():
            return True
        else:
            return False
    def get(self):
        if not self._check_login():
            return self.response.out.write(open('login.html').read())

        items_query = Item.all()
        items = sorted(items_query.fetch(100), key=lambda x: (CATEGORIES.index(x.category), x.position))

        if users.is_current_user_admin():
            url = users.create_logout_url(self.request.uri)
            url_linktext = 'Logout'
            can_edit = True
        else:
            url = users.create_login_url(self.request.uri)
            url_linktext = 'Login'
            can_edit = False

        for item in items:
            if item.avail_parts < 1:
                item.is_complete = True
                item.avail_parts = 0
            item.visible = (item.tot_parts > 0)

        template_values = {
            'items': items,
            'url': url,
            'url_linktext': url_linktext,
            'can_edit': can_edit,
        }

        self.response.out.write(env.get_template('index.html').render(template_values))

class MakeGift(webapp.RequestHandler):
    def post(self):
        if self.request.get("num_items"):
            num_items = int(self.request.get("num_items"))

            cart = []
            for i in range(num_items):
                key = self.request.get("item_key_%d" % i)
                dbitem = Item.get(key)
                item = {
                    'key': key,
                    'name': self.request.get("item_name_%d" % i),
                    'quantity': 1, #int(self.request.get("item_quantity_%d" % i)),
                    'price': float(self.request.get("item_price_%d" % i)),
                    'info': dbitem.info,
                }
                cart.append(item)

            template_values = {
                'cart': cart, # lista di dizionari
                'cart_js': urllib.quote(json.dumps(cart)),
                'total': sum(i['price']*i['quantity'] for i in cart),
                'code': 'ID'+hex(int(time.time()))[-5:].upper(),
            }

            self.response.out.write(env.get_template('makegift.html').render(template_values))
        elif self.request.get("cart"):
            gift = Gift()
            gift.email = self.request.get("email")
            gift.sender = self.request.get("sender")
            gift.message = self.request.get("message")
            gift.code = self.request.get("code")
            gift.online = (self.request.get("consegna") == "online")
            gift.cart = self.request.get("cart")
            gift.total = float(self.request.get("total"))
            gift.put()
            cart = json.loads(urllib.unquote(gift.cart))
            for c, elem in enumerate(cart):
                item = Item.get(elem['key'])
                item.avail_parts -= elem['quantity']
                item.put()
                cart[c]['info'] = item.info
            mail_confirm(gift, cart)
            self.redirect('/thanks')

def mail_confirm(gift, cart):
    sender = "Elisa e Matteo <elisamatteo@listanascita.appspotmail.com>"
    reply_to = "Matteo Bertini <matteo.bertini@gmail.com>"
    subject = "Lista nascita Matteo Elisa"
    to = "%s <%s>" % (gift.sender, gift.email)
    template_values = {
            'gift': gift,
            'cart': cart,
            }
    html = env.get_template('confirm_email.template').render(template_values)
    body = html2text.HTML2Text().handle(html)

    mail.send_mail(sender, to, subject, body, reply_to=reply_to, html=html)
    mail.send_mail_to_admins(reply_to, subject + " (%s)" % gift.sender, body, reply_to=gift.email, html=html)

class EditItem(webapp.RequestHandler):
    def get(self):
        key = self.request.get("key")
        if key:
            item = Item.get(key)
        else:
            item = {'position': 0,
                    'key': None}
        template_values = {
            'item': item,
            'categories': CATEGORIES,
        }
        self.response.out.write(env.get_template('edit.html').render(template_values))
    def post(self):
        key = self.request.get("key")
        if key:
            item = Item.get(key)
        else:
            item = Item()

        img = self.request.get("img")
        if img:
            item.image = db.Blob(img)

        item.title = self.request.get('title')
        item.info = self.request.get('info')
        item.category = self.request.get('category')
        item.position = int(self.request.get('position'))
        item.tot_parts = int(self.request.get('tot_parts'))
        item.avail_parts = int(self.request.get('avail_parts') or item.tot_parts)
        item.part_price = float(self.request.get('part_price'))
        item.tot_price = item.part_price * item.tot_parts

        item.put()
        self.redirect('/')

class Image(webapp.RequestHandler):
    def get(self, key):
        item = Item.get(key)
        if item and item.image:
            self.response.headers['Content-Type'] = "image/jpeg"
            self.response.out.write(item.image)
        else:
            self.error(404)

class Thanks(webapp.RequestHandler):
    def get(self):
        self.response.out.write(env.get_template("grazie.html").render({}))


application = webapp.WSGIApplication(
                                     [
                                      ('/', MainPage),
                                      (r'/img/(.*)\.jpg', Image),
                                      ('/edit', EditItem),
                                      ('/confirm', MakeGift),
                                      ('/thanks', Thanks),
                                      ],
                                     debug=True)
