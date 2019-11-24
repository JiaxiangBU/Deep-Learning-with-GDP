# modify from https://github.com/tensorflow/privacy/blob/master/tutorials/mnist_dpsgd_tutorial.py
"""Training a deep NN on IMDB reviews with differentially private Adam optimizer."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
import os
import tensorflow as tf
from scipy.stats import norm
import pickle

from tensorflow_privacy.privacy.analysis.rdp_accountant import compute_rdp
from tensorflow_privacy.privacy.analysis.rdp_accountant import get_privacy_spent
from tensorflow_privacy.privacy.optimizers import dp_optimizer

from privacy_accountants import *

from keras.preprocessing import sequence
#### FLAGS
tf.flags.DEFINE_boolean('dpsgd', True, 'If True, train with DP-SGD. If False, '
                        'train with vanilla SGD.')
tf.flags.DEFINE_float('learning_rate', 0.02, 'Learning rate for training')
tf.flags.DEFINE_float('noise_multiplier', 0.56,
                      'Ratio of the standard deviation to the clipping norm')
#object_file = pickle.load(open("IMDBresult_0.71",'rb'))
tf.flags.DEFINE_float('l2_norm_clip', 1, 'Clipping norm')
tf.flags.DEFINE_integer('epochs', 25, 'Number of epochs')
tf.flags.DEFINE_string('model_dir', None, 'Model directory')

FLAGS = tf.flags.FLAGS

microbatches=512
np.random.seed(0)
tf.set_random_seed(0)

max_features = 10000
# cut texts after this number of words (among top max_features most common words)
maxlen = 256


def rnn_model_fn(features, labels, mode):

  # Define CNN architecture using tf.keras.layers.
  input_layer = tf.reshape(features['x'], [-1,maxlen])
  y = tf.keras.layers.Embedding(max_features,16).apply(input_layer)
  y=tf.keras.layers.GlobalAveragePooling1D().apply(y)
  y=  tf.keras.layers.Dense(16, activation='relu').apply(y)
  logits=  tf.keras.layers.Dense(2).apply(y)
  
  # Calculate loss as a vector (to support microbatches in DP-SGD).
  vector_loss = tf.nn.sparse_softmax_cross_entropy_with_logits(
      labels=labels, logits=logits)
  # Define mean of loss across minibatch (for reporting through tf.Estimator).
  scalar_loss = tf.reduce_mean(vector_loss)

  # Configure the training op (for TRAIN mode).
  if mode == tf.estimator.ModeKeys.TRAIN:

    if FLAGS.dpsgd:
      # Use DP version of GradientDescentOptimizer. Other optimizers are
      # available in dp_optimizer. Most optimizers inheriting from
      # tf.train.Optimizer should be wrappable in differentially private
      # counterparts by calling dp_optimizer.optimizer_from_args().
      optimizer = dp_optimizer.DPAdamGaussianOptimizer(
          l2_norm_clip=FLAGS.l2_norm_clip,
          noise_multiplier=FLAGS.noise_multiplier,
          num_microbatches=microbatches,
          learning_rate=FLAGS.learning_rate)
      opt_loss = vector_loss
    else:
      optimizer = tf.train.AdamOptimizer(
          learning_rate=FLAGS.learning_rate)
      opt_loss = scalar_loss
    
    global_step = tf.train.get_global_step()
    train_op = optimizer.minimize(loss=opt_loss, global_step=global_step)
    # In the following, we pass the mean of the loss (scalar_loss) rather than
    # the vector_loss because tf.estimator requires a scalar loss. This is only
    # used for evaluation and debugging by tf.estimator. The actual loss being
    # minimized is opt_loss defined above and passed to optimizer.minimize().
    return tf.estimator.EstimatorSpec(mode=mode,
                                      loss=scalar_loss,
                                      train_op=train_op)

  # Add evaluation metrics (for EVAL mode).
  elif mode == tf.estimator.ModeKeys.EVAL:
    eval_metric_ops = {
        'accuracy':
            tf.metrics.accuracy(
                labels=labels,
                predictions=tf.argmax(input=logits, axis=1))
    }
    return tf.estimator.EstimatorSpec(mode=mode,
                                      loss=scalar_loss,
                                      eval_metric_ops=eval_metric_ops)



def load_imdb():
  (train_data,train_labels), (test_data,test_labels) = tf.keras.datasets.imdb.load_data(num_words=max_features)

  train_data = sequence.pad_sequences(train_data, maxlen=maxlen).astype('float32')
  test_data = sequence.pad_sequences(test_data, maxlen=maxlen).astype('float32')
  return train_data,train_labels,test_data,test_labels


def main(unused_argv):
  tf.logging.set_verbosity(3)

  # Load training and test data.
  train_data,train_labels,test_data,test_labels = load_imdb()

  # Instantiate the tf.Estimator.
  imdb_classifier = tf.estimator.Estimator(model_fn=rnn_model_fn,
                                            model_dir=FLAGS.model_dir)

  # Create tf.Estimator input functions for the training and test data.
  eval_input_fn = tf.estimator.inputs.numpy_input_fn(
      x={'x': test_data},
      y=test_labels,
      num_epochs=1,
      shuffle=False)

  # Training loop.
  steps_per_epoch = 25000 // 512
  test_accuracy_list = []

  for epoch in range(1, FLAGS.epochs + 1):
    np.random.seed(epoch)
    for step in range(steps_per_epoch):
        tf.set_random_seed(0)
        whether=np.random.random_sample(25000)>(1-512/25000)
        subsampling=[i for i in np.arange(25000) if whether[i]]
        global microbatches
        microbatches=len(subsampling)

        train_input_fn = tf.estimator.inputs.numpy_input_fn(
          x={'x': train_data[subsampling]},
          y=train_labels[subsampling],
          batch_size=len(subsampling),
          num_epochs=1,
          shuffle=False)
        # Train the model for one step.
        imdb_classifier.train(input_fn=train_input_fn, steps=1)

    # Evaluate the model and print results
    eval_results = imdb_classifier.evaluate(input_fn=eval_input_fn)
    test_accuracy = eval_results['accuracy']
    test_accuracy_list.append(test_accuracy)
    print('Test accuracy after %d epochs is: %.3f' % (epoch, test_accuracy))
    
    # Compute the privacy budget expended so far.
    if FLAGS.dpsgd:
      eps = compute_epsP(epoch,FLAGS.noise_multiplier,25000,512,1e-5)
      mu= compute_muP(epoch,FLAGS.noise_multiplier,25000,512)
      print('For delta=1e-5, the current epsilon is: %.2f' % eps)
      print('For delta=1e-5, the current mu is: %.2f' % mu)
      
      if mu>2:
        break
    else:
      print('Trained with vanilla non-private SGD optimizer')

    # pickle accuracy,epsilon,mu
    if FLAGS.dpsgd:
      fffff=open(os.getcwd() + '/PoissonIMDBresult_'+str(FLAGS.noise_multiplier)+ '.pkl', 'wb')
    else:
      fffff=open(os.getcwd() + '/PoissonIMDBresult_nonprivate.pkl', 'wb')
    pickle.dump((test_accuracy_list),fffff)
    fffff.close()
    
if __name__ == '__main__':
  tf.app.run()
