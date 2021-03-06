from __future__ import print_function

from keras.layers import BatchNormalization, Concatenate, Conv2D, Conv2DTranspose, Dropout, MaxPooling2D, UpSampling2D, \
    add
from keras.models import Input, Model
from keras.regularizers import l2

from model.connections import get_logger
from resnet101 import ResNet101

logger = print

class BaseUNet(object):
	"""A Keras Unet model based on the https://arxiv.org/abs/1505.04597 paper.
	It is the state of the art for semantic segmentation. Highly customizable.

	# Arguments:
		input_shape: shape of input image (h, w)
		in_channles: number of channle in input image
		out_channels: number of channels in mask (must match training data output)
		num_filters: number of filters in the first layer after the input layer
		depth: number of layers in the encoder/decoder
	"""
    def build_model(self, input_shape, in_channels=3, out_channels=1, num_filters=64, depth=4, activation='relu',
                    dropout=0.5, batch_norm=False, maxpool=True, is_deconv=True, residual=False, inc_rate=2.,
                    l2_reg=0.0001):
        input_shape = input_shape + (in_channels,)
        input_tensor = Input(shape=input_shape)
        blocks = self.level_block(input_tensor, num_filters, depth, inc_rate, activation, dropout, batch_norm, maxpool,
                                  is_deconv, residual, l2_reg)
        output = Conv2D(out_channels, (1, 1), activation='sigmoid', kernel_regularizer=l2(l2_reg))(blocks)
        return Model(inputs=input_tensor, outputs=output)

    def conv_block(self, m, dim, acti, bn, res, reg, do=0):
        n = Conv2D(dim, 3, activation=acti, padding='same', kernel_regularizer=l2(reg))(m)
        n = BatchNormalization()(n) if bn else n
        n = Dropout(do)(n) if do else n
        n = Conv2D(dim, 3, activation=acti, padding='same', kernel_regularizer=l2(reg))(n)
        n = BatchNormalization()(n) if bn else n
        return Concatenate()([m, n]) if res else n

    def level_block(self, m, dim, depth, inc, acti, do, bn, mp, up, res, reg):
        if depth > 0:
            n = self.conv_block(m, dim, acti, bn, res, reg)
            m = MaxPooling2D()(n) if mp else Conv2D(dim, 3, strides=2, padding='same', kernel_regularizer=l2(reg))(n)
            m = self.level_block(m, int(inc * dim), depth - 1, inc, acti, do, bn, mp, up, res, reg)
            if up:
                m = UpSampling2D()(m)
                m = Conv2D(dim, 2, activation=acti, padding='same', kernel_regularizer=l2(reg))(m)
            else:
                m = Conv2DTranspose(dim, 3, strides=2, activation=acti, padding='same')(m)
            n = Concatenate()([n, m])
            m = self.conv_block(n, dim, acti, bn, res, reg)
        else:
            m = self.conv_block(m, dim, acti, bn, res, reg, do)
        return m


class UNetResNet101(object):
	"""A Keras Unet model that uses the Resnet101 model as its encoder"""
    def __init__(self):
        self.encoder = None

    def build_model(self, input_shape=(256, 256), in_channels=3, out_channels=1, dropout=0.2, num_filters=32,
                    is_deconv=False, resnet_pretrained=True, l2_reg=0.0001,
                    resnet_weights_path='data/resnet101_weights.h5'):

        logger('Loading ResNet101 encoder...')
        input_shape = input_shape + (in_channels,)
        self.encoder = ResNet101(include_top=False, input_shape=(256, 256, 3), classes=out_channels)
        logger('Loaded ResNet101 encoder')

        if resnet_pretrained:
            logger('Loading ResNet101 image-net weights...')
            self.encoder.load_weights(resnet_weights_path)
            logger('Loaded ResNet101 imagenet weights')

        input_tensor = Input(input_shape)
        stage_1_layers = ['conv1_zeropadding', 'conv1', 'bn_conv1', 'scale_conv1', 'conv1_relu']
        conv1 = self.encoder.get_layer(name=stage_1_layers[0])(input_tensor)
        for layer_name in stage_1_layers[1:]:
            conv1 = self.encoder.get_layer(name=layer_name)(conv1)

        conv_2 = MaxPooling2D((2, 2), name='pool1')(conv1)

        conv_2 = self.encoder_block(conv_2, stage='2', block='a', shortcut=True)
        conv_2 = self.encoder_block(conv_2, stage='2', block='b')
        conv_2 = self.encoder_block(conv_2, stage='2', block='c')

        conv_3 = self.encoder_block(conv_2, stage='3', block='a', shortcut=True)
        for i in range(1, 3):
            conv_3 = self.encoder_block(conv_3, stage=3, block='b' + str(i))

        conv_4 = self.encoder_block(conv_3, stage='4', block='a', shortcut=True)
        for i in range(1, 23):
            conv_4 = self.encoder_block(conv_4, stage=4, block='b' + str(i))

        conv_5 = self.encoder_block(conv_4, stage=5, block='a', shortcut=True)
        conv_5 = self.encoder_block(conv_5, stage=5, block='b')
        conv_5 = self.encoder_block(conv_5, stage=5, block='c')

        pool = MaxPooling2D((2, 2))(conv_5)
        center = self.decoder_block(pool, num_filters * 8 * 2, num_filters * 8, is_deconv, l2_reg)

        dec_5 = self.decoder_block(Concatenate(axis=3)([center, conv_5]), num_filters * 8 * 2, num_filters * 8,
                                   is_deconv, l2_reg)
        dec_4 = self.decoder_block(Concatenate(axis=3)([dec_5, conv_4]), num_filters * 8 * 2, num_filters * 8,
                                   is_deconv, l2_reg)
        dec_3 = self.decoder_block(Concatenate(axis=3)([dec_4, conv_3]), num_filters * 4 * 2, num_filters * 2,
                                   is_deconv, l2_reg)
        dec_2 = self.decoder_block(Concatenate(axis=3)([dec_3, conv_2]), num_filters * 2 * 2, num_filters * 2 * 2,
                                   is_deconv, l2_reg)
        dec_1 = self.decoder_block(dec_2, num_filters * 2 * 2, num_filters, is_deconv, l2_reg)
        dec_0 = Conv2D(num_filters, (3, 3), activation='relu', padding='same', kernel_regularizer=l2(l2_reg))(dec_1)

        dropped = Dropout(dropout)(dec_0)
        output = Conv2D(out_channels, (1, 1), kernel_regularizer=l2(l2_reg), activation='sigmoid')(dropped)
        return Model(inputs=input_tensor, outputs=output)

    def encoder_block(self, input_tensor, stage, block, shortcut=False):
        """
        The identity_block is the block that has no conv layer at shortcut
        # Arguments
            input_tensor: input_tensor tensor
            kernel_size: default 3, the kernel size of middle conv layer at main
                path
            filters: list of integers, the nb_filters of 3 conv layer at main path
            stage: integer, current stage label, used for generating layer names
            block: 'a','b'..., current block label, used for generating layer names
        """
        conv_name_base = 'res' + str(stage) + block + '_branch'
        bn_name_base = 'bn' + str(stage) + block + '_branch'
        scale_name_base = 'scale' + str(stage) + block + '_branch'

        x = self.encoder.get_layer(name=conv_name_base + '2a')(input_tensor)
        x = self.encoder.get_layer(name=bn_name_base + '2a')(x)
        x = self.encoder.get_layer(name=scale_name_base + '2a')(x)
        x = self.encoder.get_layer(name=conv_name_base + '2a_relu')(x)

        x = self.encoder.get_layer(name=conv_name_base + '2b_zeropadding')(x)
        x = self.encoder.get_layer(name=conv_name_base + '2b')(x)
        x = self.encoder.get_layer(name=bn_name_base + '2b')(x)
        x = self.encoder.get_layer(name=scale_name_base + '2b')(x)
        x = self.encoder.get_layer(name=conv_name_base + '2b_relu')(x)

        x = self.encoder.get_layer(name=conv_name_base + '2c')(x)
        x = self.encoder.get_layer(name=bn_name_base + '2c')(x)
        x = self.encoder.get_layer(name=scale_name_base + '2c')(x)

        if shortcut:
            branch = self.encoder.get_layer(name=conv_name_base + '1')(input_tensor)
            branch = self.encoder.get_layer(name=bn_name_base + '1')(branch)
            branch = self.encoder.get_layer(name=scale_name_base + '1')(branch)
        else:
            branch = input_tensor

        x = add([x, branch], name='res' + str(stage) + block)
        x = self.encoder.get_layer(name='res' + str(stage) + block + '_relu')(x)
        return x

    def decoder_block(self, input_tensor, middle_channels, out_channels, is_deconv=False, l2_reg=0.0001):
        if is_deconv:
            x = Conv2D(middle_channels, (3, 3), activation='relu', padding='same', kernel_regularizer=l2(l2_reg))(
                input_tensor)
            x = Conv2DTranspose(out_channels, kernel_size=(4, 4), strides=(2, 2), padding='same',
                                activation='relu', kernel_regularizer=l2(l2_reg))(x)
        else:
            x = UpSampling2D()(input_tensor)
            x = Conv2D(middle_channels, (3, 3), activation='relu', padding='same', kernel_regularizer=l2(l2_reg))(x)
            x = Conv2D(out_channels, (3, 3), activation='relu', padding='same', kernel_regularizer=l2(l2_reg))(x)
        return x
