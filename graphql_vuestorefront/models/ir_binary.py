# -*- coding: utf-8 -*-
# Copyright 2024 ODOOGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
import base64
import io
from mimetypes import guess_extension
from PIL import Image, WebPImagePlugin

from odoo import models
from odoo.http import request
from odoo.tools.safe_eval import safe_eval
from odoo.tools.image import image_process, image_guess_size_from_field_name
from odoo.tools.mimetypes import guess_mimetype, get_extension


class IrBinary(models.AbstractModel):
    _inherit = 'ir.binary'

    def _get_image_stream_from(
        self, record, field_name='raw', filename=None, filename_field='name',
        mimetype=None, default_mimetype='image/png', placeholder=None,
        width=0, height=0, crop=False, quality=0,
    ):
        stream = super()._get_image_stream_from(record=record, field_name=field_name, filename=filename, filename_field=filename_field,
                                    mimetype=mimetype, default_mimetype=default_mimetype, placeholder=placeholder,
                                    width=width, height=height, crop=crop, quality=quality)
        if not stream or stream.size == 0:
            if not placeholder:
                placeholder = record._get_placeholder_filename(field_name)
            stream = self._get_placeholder_stream(placeholder)
        image_format = None
        if stream and stream.mimetype and ('jpg' in stream.mimetype or 'jpeg' in stream.mimetype):
            image_format = 'jpeg'
        if stream and stream.mimetype and ('png' in stream.mimetype or 'png' in stream.mimetype):
            image_format = 'png'
        if stream and stream.mimetype and 'webp' in stream.mimetype:
            image_format = 'webp'
        if not stream.data:
            stream.data = stream.read()
        if image_format:
            if stream.data and width and height:
                image_base64 = stream.data
                #TODO remove when odoo fix webp resize issue
                # FIX: convert webp to png and then resize it
                if image_format == 'webp':
                    new_image_base64 = self.webp_base64_to_png(stream.read())
                    image_base64 = image_process(
                        new_image_base64,
                        size=(width, height),
                        crop=crop,
                        quality=quality,
                    )

                img = Image.open(io.BytesIO(image_base64))

                ICP = request.env['ir.config_parameter'].sudo()
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')

                # Get background color from context or settings
                try:
                    if self.env.context.get('background_rgba'):
                        background_rgba = safe_eval(self.env.context.get('background_rgba'))
                    else:
                        background_rgba = safe_eval(ICP.get_param('vsf_image_background_rgba', '(255, 255, 255, 255)'))
                except:
                    background_rgba = (66, 28, 82)
                # Create a new background, merge the background with the image centered
                img_w, img_h = img.size
                if image_format in ['jpeg', 'png']:
                    background = Image.new('RGB', (width, height), background_rgba[:3])
                else:
                    background = WebPImagePlugin.Image.new('RGBA', (width, height), background_rgba)
                bg_w, bg_h = background.size
                offset = ((bg_w - img_w) // 2, (bg_h - img_h) // 2)
                background.paste(img, offset)

                # Get compression quality from settings
                quality = ICP.get_param('vsf_image_quality', 100)

                stream_image = io.BytesIO()
                if image_format in ['jpeg', 'png']:
                    background.save(stream_image, format="WEBP", quality=quality)
                    stream_image.seek(0)
                else:
                    background.save(stream_image, format=image_format.upper(), quality=quality, subsampling=0)

                image_base64 = base64.b64encode(stream_image.getvalue())

                # Response
                stream.data = base64.b64decode(image_base64)

            self._update_download_name(record, stream, filename, field_name, filename_field, f'image/webp', default_mimetype)
        return stream

    def _update_download_name(self, record, stream, filename, field_name, filename_field, mimetype, default_mimetype):
        if stream.type in ('data', 'path'):
            if mimetype:
                stream.mimetype = mimetype
            elif not stream.mimetype:
                if stream.type == 'data':
                    head = stream.data[:1024]
                else:
                    with open(stream.path, 'rb') as file:
                        head = file.read(1024)
                stream.mimetype = guess_mimetype(head, default=default_mimetype)

            if filename:
                stream.download_name = filename
            elif filename_field in record:
                stream.download_name = record[filename_field]
            if not stream.download_name:
                stream.download_name = f'{record._table}-{record.id}-{field_name}'

            stream.download_name = stream.download_name.replace('\n', '_').replace('\r', '_')
            if (not get_extension(stream.download_name)
                and stream.mimetype != 'application/octet-stream'):
                stream.download_name += guess_extension(stream.mimetype) or ''

    def webp_base64_to_png(self, image_bytes):
        """
        Converts a WebP image encoded in base64 to a PNG image.
        """

        # Create an Image object from the bytes
        image = Image.open(io.BytesIO(image_bytes))

        # Convert the image to PNG format
        image = image.convert('RGB')

        stream = io.BytesIO()
        # Save the image as a PNG
        image.save(stream, format='PNG')

        return stream.getvalue()