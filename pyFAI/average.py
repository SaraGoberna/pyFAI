#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#    Project: Fast Azimuthal integration
#             https://github.com/pyFAI/pyFAI
#
#    Copyright (C) European Synchrotron Radiation Facility, Grenoble, France
#
#    Principal author:       Jérôme Kieffer (Jerome.Kieffer@ESRF.eu)
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

"""

Utilities, mainly for image treatment

"""

__author__ = "Jerome Kieffer"
__contact__ = "Jerome.Kieffer@ESRF.eu"
__license__ = "GPLv3+"
__copyright__ = "European Synchrotron Radiation Facility, Grenoble, France"
__date__ = "04/08/2016"
__status__ = "production"

import logging
import numpy
import fabio

try:
    from .third_party import six
except (ImportError, Exception):
    import six

from .utils import removeSaturatedPixel
from .utils import exists

from ._version import calc_hexversion
if ("hexversion" not in dir(fabio)) or (fabio.hexversion < calc_hexversion(0, 4, 0, "dev", 5)):
    # Short cut fabio.factory do not exists on older versions
    fabio.factory = fabio.fabioimage.FabioImage.factory

logger = logging.getLogger("pyFAI.average")


class ImageReductionFilter(object):
    """
    Generic filter applyed in a set of images.
    """

    def add_image(self, image):
        """
        Add an image to the filter.

        @param image numpy.ndarray: image to add
        """
        raise NotImplementedError()

    def get_result(self):
        """
        Get the result of the filter.

        @return: result filter
        """
        raise NotImplementedError()


class ImageAccumulatorFilter(ImageReductionFilter):
    """
    Filter applyed in a set of images in which it is possible
    to reduce data step by step into a single merged image.
    """

    def __init__(self):
        self._count = 0
        self._accumulated_image = None

    def add_image(self, image):
        """
        Add an image to the filter.

        @param image numpy.ndarray: image to add
        """
        self._accumulated_image = self._accumulate(self._accumulated_image, image)
        self._count += 1

    def _accumulate(self, accumulated_image, added_image):
        """
        Add an image to the filter.

        @param accumulated_image numpy.ndarray: image use to accumulate information
        @param added_image numpy.ndarray: image to add
        """
        raise NotImplementedError()

    def get_result(self):
        """
        Get the result of the filter.

        @return: result filter
        @rtype: numpy.ndarray
        """
        return self._accumulated_image


class MaxAveraging(ImageAccumulatorFilter):
    name = "max"

    def _accumulate(self, accumulated_image, added_image):
        if accumulated_image is None:
            return added_image
        return numpy.maximum(accumulated_image, added_image)


class MinAveraging(ImageAccumulatorFilter):
    name = "min"

    def _accumulate(self, accumulated_image, added_image):
        if accumulated_image is None:
            return added_image
        return numpy.minimum(accumulated_image, added_image)


class SumAveraging(ImageAccumulatorFilter):
    name = "sum"

    def _accumulate(self, accumulated_image, added_image):
        if accumulated_image is None:
            return added_image
        return accumulated_image + added_image


class MeanAveraging(SumAveraging):
    name = "mean"

    def get_result(self):
        return self._accumulated_image / numpy.float32(self._count)


_FILTERS = [
    MaxAveraging,
    MinAveraging,
    MeanAveraging,
    SumAveraging,
]

_FILTER_NAME_MAPPING = {}
for f in _FILTERS:
    _FILTER_NAME_MAPPING[f.name] = f


def _get_filter_class(filter_name):
    global _FILTER_NAME_MAPPING
    filter_class = _FILTER_NAME_MAPPING.get(filter_name, None)
    if filter_class is None:
        raise Exception("Filter name '%s' unknown" % filter_name)
    return filter_class


def average_dark(lstimg, center_method="mean", cutoff=None, quantiles=(0.5, 0.5)):
    """
    Averages a serie of dark (or flat) images.
    Centers the result on the mean or the median ...
    but averages all frames within  cutoff*std

    @param lstimg: list of 2D images or a 3D stack
    @param center_method: is the center calculated by a "mean", "median", "quantile", "std"
    @param cutoff: keep all data where (I-center)/std < cutoff
    @param quantiles: 2-tuple of floats average out data between the two quantiles

    @return: 2D image averaged
    """
    if "ndim" in dir(lstimg) and lstimg.ndim == 3:
        stack = lstimg.astype(numpy.float32)
        shape = stack.shape[1:]
        length = stack.shape[0]
    else:
        shape = lstimg[0].shape
        length = len(lstimg)
        if length == 1:
            return lstimg[0].astype(numpy.float32)
        stack = numpy.zeros((length, shape[0], shape[1]), dtype=numpy.float32)
        for i, img in enumerate(lstimg):
            stack[i] = img
    if center_method in dir(stack):
        center = stack.__getattribute__(center_method)(axis=0)
    elif center_method == "median":
        logger.info("Filtering data (median)")
        center = numpy.median(stack, axis=0)
    elif center_method.startswith("quantil"):
        logger.info("Filtering data (quantiles: %s)", quantiles)
        sorted_ = numpy.sort(stack, axis=0)
        lower = max(0, int(numpy.floor(min(quantiles) * length)))
        upper = min(length, int(numpy.ceil(max(quantiles) * length)))
        if (upper == lower):
            if upper < length:
                upper += 1
            elif lower > 0:
                lower -= 1
            else:
                logger.warning("Empty selection for quantil %s, would keep points from %s to %s", quantiles, lower, upper)
        center = sorted_[lower:upper].mean(axis=0)
    else:
        raise RuntimeError("Cannot understand method: %s in average_dark" % center_method)
    if cutoff is None or cutoff <= 0:
        output = center
    else:
        std = stack.std(axis=0)
        strides = 0, std.strides[0], std.strides[1]
        std.shape = 1, shape[0], shape[1]
        std.strides = strides
        center.shape = 1, shape[0], shape[1]
        center.strides = strides
        mask = ((abs(stack - center) / std) > cutoff)
        stack[numpy.where(mask)] = 0.0
        summed = stack.sum(axis=0)
        output = summed / numpy.maximum(1, (length - mask.sum(axis=0)))
    return output


class MonitorNotFound(Exception):
    """Raised when monitor information in not found or is not valid."""
    pass


def _get_monitor_value_from_edf(image, monitor_key):
    """Return the monitor value from an EDF image using an header key.

    Take care of the counter and motor syntax using for example 'counter/bmon'
    which reach 'bmon' value from 'counter_pos' key using index from
    'counter_mne' key.

    @param image fabio.fabioimage.FabioImage: Image containing the header
    @param monitor_key str: Key containing the monitor
    @return: returns the monitor else raise a MonitorNotFound
    @rtype: float
    @raise MonitorNotFound: when the expected monitor is not found on the header
    """
    keys = image.header

    if "/" in monitor_key:
        base_key, mnemonic = monitor_key.split('/', 1)

        mnemonic_values_key = base_key + "_mne"
        mnemonic_values =  keys.get(mnemonic_values_key, None)
        if mnemonic_values is None:
            raise MonitorNotFound("Monitor mnemonic key '%s' not found in the header" % (mnemonic_values_key))

        mnemonic_values = mnemonic_values.split()
        pos_values_key = base_key + "_pos"
        pos_values =  keys.get(pos_values_key)
        if pos_values is None:
            raise MonitorNotFound("Monitor pos key '%s' not found in the header" % (pos_values_key))

        pos_values = pos_values.split()

        try:
            index = mnemonic_values.index(mnemonic)
        except ValueError as _e:
            logger.debug("Exception", exc_info=1)
            raise MonitorNotFound("Monitor mnemonic '%s' not found in the header key '%s'" % (mnemonic, mnemonic_values_key))

        if index >= len(pos_values):
            raise MonitorNotFound("Monitor value '%s' not found in '%s'. Not enougth values." % (pos_values_key))

        monitor = pos_values[index]

    else:
        if monitor_key not in keys:
            raise MonitorNotFound("Monitor key '%s' not found in the header" % (monitor_key))
        monitor = keys[monitor_key]

    try:
        monitor = float(monitor)
    except ValueError as _e:
        logger.debug("Exception", exc_info=1)
        raise MonitorNotFound("Monitor value '%s' is not valid" % (monitor))
    return monitor


def _get_monitor_value(image, monitor_key):
    """Return the monitor value from an image using an header key.

    @param image fabio.fabioimage.FabioImage: Image containing the header
    @param monitor_key str: Key containing the monitor
    @return: returns the monitor else raise an exception
    @rtype: float
    @raise MonitorNotFound: when the expected monitor is not found on the header
    """
    if monitor_key is None:
        return Exception("No monitor defined")

    if isinstance(image, fabio.edfimage.EdfImage):
        return _get_monitor_value_from_edf(image, monitor_key)
    elif isinstance(image, fabio.numpyimage.numpyimage):
        return _get_monitor_value_from_edf(image, monitor_key)
    else:
        raise Exception("File format '%s' unsupported" % type(image))


def average_images(listImages, output=None, threshold=0.1, minimum=None, maximum=None,
                  darks=None, flats=None, filter_="mean", correct_flat_from_dark=False,
                  cutoff=None, quantiles=None, fformat="edf", monitor_key=None):
    """
    Takes a list of filenames and create an average frame discarding all saturated pixels.

    @param listImages: list of string representing the filenames
    @param output: name of the optional output file
    @param threshold: what is the upper limit? all pixel > max*(1-threshold) are discareded.
    @param minimum: minimum valid value or True
    @param maximum: maximum valid value
    @param darks: list of dark current images for subtraction
    @param flats: list of flat field images for division
    @param filter_: can be "min", "max", "median", "mean", "sum", "quantiles" (default='mean')
    @param correct_flat_from_dark: shall the flat be re-corrected ?
    @param cutoff: keep all data where (I-center)/std < cutoff
    @param quantiles: 2-tuple containing the lower and upper quantile (0<q<1) to average out.
    @param fformat: file format of the output image, default: edf
    @param monitor_key str: Key containing the monitor. Can be none.
    @return: filename with the data or the data ndarray in case format=None
    """
    def correct_img(image, data):
        "internal subfunction for dark/flat/monitor "
        corrected_img = numpy.ascontiguousarray(data, numpy.float32)
        if threshold or minimum or maximum:
            corrected_img = removeSaturatedPixel(corrected_img, threshold, minimum, maximum)
        if do_dark:
            corrected_img -= dark
        if do_flat:
            corrected_img /= flat
        if monitor_key is not None:
            try:
                monitor = _get_monitor_value(image, monitor_key)
                corrected_img /= monitor
            except MonitorNotFound as e:
                logger.warning("Monitor not found in filename '%s', data skipped. Cause: %s", image.filename, str(e))
                return None
        return corrected_img
    # input sanitization
    if filter_ not in ["min", "max", "median", "mean", "sum", "quantiles", "std"]:
        logger.warning("Filter %s not understood. switch to mean filter", filter_)
        filter_ = "mean"

    nb_files = len(listImages)
    nb_frames = 0
    fimgs = []

    for fn in listImages:
        if isinstance(fn, six.string_types):
            logger.info("Reading %s", fn)
            fimg = fabio.open(fn)
        elif isinstance(fn, fabio.fabioimage.fabioimage):
            fimg = fn
        else:
            if fabio.hexversion < 262148:
                logger.error("Old version of fabio detected, upgrade to 0.4 or newer")

            # Assume this is a numpy array like
            if not ("ndim" in dir(fn) and "shape" in dir(fn)):
                raise RuntimeError("Not good type for input, got %s, expected numpy array" % type(fn))
            fimg = fabio.numpyimage.NumpyImage(data=fn)
        fimgs.append(fimg)
        nb_frames += fimg.nframes

    do_dark = (darks is not None)
    do_flat = (flats is not None)
    dark = None
    flat = None

    if do_dark:
        if "ndim" in dir(darks) and darks.ndim == 3:
            dark = average_dark(darks, center_method="mean", cutoff=4)
        elif ("__len__" in dir(darks)) and isinstance(darks[0], six.string_types):
            dark = average_dark([fabio.open(f).data for f in darks if exists(f)], center_method="mean", cutoff=4)
        elif ("__len__" in dir(darks)) and ("ndim" in dir(darks[0])) and (darks[0].ndim == 2):
            dark = average_dark(darks, center_method="mean", cutoff=4)
    if do_flat:
        if "ndim" in dir(flats) and flats.ndim == 3:
            flat = average_dark(flats, center_method="mean", cutoff=4)
        elif ("__len__" in dir(flats)) and isinstance(flats[0], six.string_types):
            flat = average_dark([fabio.open(f).data for f in flats if exists(f)], center_method="mean", cutoff=4)
        elif ("__len__" in dir(flats)) and ("ndim" in dir(flats[0])) and (flats[0].ndim == 2):
            flat = average_dark(flats, center_method="mean", cutoff=4)
        else:
            logger.warning("there is some wrong with flats=%s", flats)
        if correct_flat_from_dark:
            flat -= dark
        flat[numpy.where(flat <= 0)] = 1.0

    if (cutoff or quantiles or (filter_ in ["median", "quantiles", "std"])):
        first_frame = fimgs[0]
        first_shape = first_frame.data.shape
        logger.info("Big array allocation for median filter/cut-off/quantiles %i*%i*%i", first_frame.nframes, first_frame.dim2, first_frame.dim1)
        big_img = numpy.zeros((nb_frames, first_shape[0], first_shape[1]), dtype=numpy.float32)
        idx = 0
        for fimg in fimgs:
            for frame in range(fimg.nframes):
                if fimg.nframes == 1:
                    ds = fimg.data
                else:
                    ds = fimg.getframe(frame).data
                data = correct_img(fimg, ds)
                if data is None:
                    # skip inconsistante data
                    continue
                big_img[idx] = data
                idx += 1
        big_img.resize((idx, first_shape[0], first_shape[1]))
        datared = average_dark(big_img, filter_, cutoff, quantiles)
    else:
        filter_class = _get_filter_class(filter_)
        accumulator = filter_class()
        for fabio_image in fimgs:
            for frame in range(fabio_image.nframes):
                if fabio_image.nframes == 1:
                    data = fabio_image.data
                else:
                    data = fabio_image.getframe(frame).data
                logger.debug("Intensity range for %s#%i is %s --> %s", fabio_image.filename, frame, data.min(), data.max())

                corrected_image = correct_img(fabio_image, data)
                if corrected_image is None:
                    continue
                accumulator.add_image(corrected_image)

        datared = accumulator.get_result()

    logger.debug("Intensity range in merged dataset : %s --> %s", datared.min(), datared.max())
    if fformat is not None:
        if fformat.startswith("."):
            fformat = fformat.lstrip(".")
        if (output is None):
            prefix = ""
            for ch in zip(i.filename for i in fimgs):
                c = ch[0]
                good = True
                if c in ["*", "?", "[", "{", "("]:
                    good = False
                    break
                for i in ch:
                    if i != c:
                        good = False
                        break
                if good:
                    prefix += c
                else:
                    break
            output = "%sfilt%02i-%s.%s" % (filter_, nb_frames, prefix, fformat)

    if fformat and output:
        if "." in fformat:  # in case "edf.gz"
            fformat = fformat.split(".")[0]
        fabioclass = fabio.factory(fformat + "image")
        header = fabio.fabioimage.OrderedDict()
        header["method"] = filter_
        header["nfiles"] = nb_files
        header["nframes"] = nb_frames
        header["cutoff"] = str(cutoff)
        header["quantiles"] = str(quantiles)
        form = "merged_file_%%0%ii" % len(str(len(fimgs)))
        for i, f in enumerate(fimgs):
            name = form % i
            header[name] = f.filename
        fimg = fabioclass.__class__(data=datared, header=header)
        fimg.write(output)
        logger.info("Wrote %s", output)
        return output
    else:
        return datared
