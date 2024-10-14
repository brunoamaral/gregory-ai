
"""
This model contains utility functions for pseudo-labelling with BERT and self-training 
and co-training approaches for traditional ML models.
"""

import numpy as np
import pandas as pd
import os
import tensorflow as tf
from transformers import BertTokenizer, TFBertModel
from tensorflow.keras.layers import Input, Dense, Lambda
from tensorflow.keras.models import Model
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.callbacks import EarlyStopping
from tensorflow.keras.utils import to_categorical
from sklearn.semi_supervised import SelfTrainingClassifier
from scipy.sparse import vstack


def encode_texts(tokenizer, texts, max_len=128):
    
    """
    Encode a list of texts into input IDs and attention masks using a tokenizer.

    Parameters:
    tokenizer (Tokenizer): The tokenizer to use for encoding the texts.
    texts (list of str): A list of texts to be encoded.
    max_len (int, optional): The maximum length of the encoded texts. Default is 128.
                             Must be equal to the max_length parameter used when training the model.

    Returns:
    tuple: A tuple containing two elements:
        - input_ids (tf.Tensor): A tensor of input IDs.
        - attention_masks (tf.Tensor): A tensor of attention masks.
    """
    input_ids = []
    attention_masks = []

    for text in texts:
        encoded = tokenizer.encode_plus(
            text,
            add_special_tokens=True,  # Adds '[CLS]' and '[SEP]'
            max_length=max_len,
            truncation=True,
            padding='max_length',
            return_attention_mask=True,
            return_tensors='tf'
        )
        input_ids.append(encoded['input_ids'])
        attention_masks.append(encoded['attention_mask'])
    
    # Convert lists to tensors
    input_ids = tf.concat(input_ids, axis=0)
    attention_masks = tf.concat(attention_masks, axis=0)

    return input_ids, attention_masks


def create_bert_uncased_model(dense_units=48, learning_rate=2e-5, max_len=128):

    """
    Create a BERT-based model for text classification.

    Parameters:
    dense_units (int, optional): The number of units in the dense layer. Default is 48.
    learning_rate (float, optional): The learning rate for the optimizer. Default is 1e-5.
    max_len (int, optional): The maximum length of input sequences. Default is 128.

    Returns:
    Model: A compiled Keras model with BERT and additional dense layers for classification.
    """
    bert_model = TFBertModel.from_pretrained('bert-base-uncased')

    # Inputs (converted to tensorflow tensors from keras tensors)
    input_ids = Input(shape=(max_len,), dtype=tf.int32, name="input_ids")
    attention_masks = Input(shape=(max_len,), dtype=tf.int32, name="attention_masks")

    # BERT layer
    bert_output = bert_model(input_ids, attention_mask=attention_masks)[1]

    # Classification layer
    classification_output = Dense(dense_units, activation='relu')(bert_output)
    classification_output = Dense(2, activation='softmax')(classification_output)

    # Final model assembly
    model = Model(inputs=[input_ids, attention_masks], outputs=classification_output)
    model.compile(optimizer=Adam(learning_rate=learning_rate), loss='categorical_crossentropy', metrics=['accuracy'])

    return model

def save_model_and_pseudos_bert(model, pseudo_labels_df):

    """
    Save the BERT model and pseudo-labelled data to disk.

    Parameters:
    model (Model): The trained BERT model.
    pseudo_labels_df (pd.DataFrame): DataFrame containing pseudo-labelled data.
    """

    # Make sure there is a models folder and a pseudo_labelling subfolder
    current_dir = os.getcwd()
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.chdir(base_dir)

    base_folder = 'models'
    if not os.path.exists(base_folder):
        os.makedirs(base_folder)
    
    subfolder = 'pseudo_labelling'
    if not os.path.exists(os.path.join(base_folder, subfolder)):
        os.makedirs(os.path.join(base_folder, subfolder))

    # Save the model
    model_path = os.path.join(base_folder, subfolder, 'pseudo_bert_model')
    model.save(model_path)
    print(f"Model saved to {model_path}")

    # Save the pseudo-labelled data
    pseudo_labels_path = os.path.join(base_folder, subfolder, 'pseudo_labels_bert.csv')
    pseudo_labels_df.to_csv(pseudo_labels_path)
    print(f"Pseudo-labelled data saved to {pseudo_labels_path}")
    os.chdir(current_dir)

def bert_iterative_training(tokenizer, unlabelled_data_pseudo, labelled_train_df, val_df_pseudo, dense_units=48, learning_rate=2e-5, max_len=128, confidence_threshold=0.9, max_iterations=10):

    """
    Iteratively train a BERT model on labelled data, predict on unlabelled data, and move confident predictions to the labelled set.

    Parameters:
    model: BERT model created using create_bert_uncased_model().
    tokenizer: BERT tokenizer.
    unlabelled_data_pseudo (pd.DataFrame): DataFrame with unlabelled data (pre-processed).
    labelled_train_df (pd.DataFrame): DataFrame with labelled training data (pre-processed).
    val_df_pseudo (pd.DataFrame): DataFrame with validation data (pre-processed).
    model: BERT model created using create_bert_uncased_model().
    max_len (int, optional): The maximum length of input sequences. Default is 128.
    confidence_threshold (float, optional): The confidence threshold to retain predictions. Default is 0.9.
    max_iterations (int, optional): The maximum number of iterations. Default is 10.

    Returns:
    pd.DataFrame: Updated labelled DataFrame after all iterations.
    """
    early_stopping = EarlyStopping(monitor='val_loss', patience=3)
    iteration = 0

    while len(unlabelled_data_pseudo) > 0 and iteration < max_iterations:
        model = create_bert_uncased_model(dense_units, learning_rate, max_len)
        iteration += 1

        # Re-tokenize the labelled data and validation data
        X_labelled_ids, X_labelled_masks = encode_texts(tokenizer, labelled_train_df['text_processed'].values, max_len=max_len)
        y_labelled = to_categorical(labelled_train_df['relevant'], num_classes=2)

        X_val_ids, X_val_masks = encode_texts(tokenizer, val_df_pseudo['text_processed'].values, max_len=max_len)
        y_val = to_categorical(val_df_pseudo['relevant'], num_classes=2)

        # Train the model on the labelled data
        model.fit([X_labelled_ids, X_labelled_masks], y_labelled, epochs=8, batch_size=16, verbose=2, validation_data=([X_val_ids, X_val_masks], y_val), callbacks=[early_stopping])

        # Re-tokenize the remaining unlabelled data
        X_unlabelled_ids, X_unlabelled_masks = encode_texts(tokenizer, unlabelled_data_pseudo['text_processed'].values, max_len=max_len)

        # Predict labels and confidence for the unlabelled data
        y_unlabelled_probs = model.predict([X_unlabelled_ids, X_unlabelled_masks])
        y_unlabelled_confidence = np.max(y_unlabelled_probs, axis=1)
        y_unlabelled_predicted = np.argmax(y_unlabelled_probs, axis=1)

        # Create a DataFrame with predictions and confidence scores
        if len(y_unlabelled_confidence) == len(unlabelled_data_pseudo) and len(y_unlabelled_predicted) == len(unlabelled_data_pseudo):
            df_predictions = unlabelled_data_pseudo.copy()
            df_predictions['predicted_label'] = y_unlabelled_predicted
            df_predictions['confidence'] = y_unlabelled_confidence
        else:
            raise ValueError("Mismatch in lengths of predictions and unlabelled data.")

        # Filter predictions by confidence threshold
        df_confident = df_predictions[df_predictions['confidence'] >= confidence_threshold]

        if df_confident.empty:
            print(f"No confident predictions above the threshold in iteration {iteration}. Stopping.")
            break

        # Add the confident predictions to the labelled data
        df_confident['relevant'] = df_confident['predicted_label']  # Assign predicted labels as actual labels
        labelled_train_df = pd.concat([labelled_train_df, df_confident])

        # Remove the confident predictions from the unlabelled data
        unlabelled_data_pseudo = unlabelled_data_pseudo.drop(df_confident.index)

    save_model_and_pseudos_bert(model, labelled_train_df)
    
    return labelled_train_df

def self_training(model, vectorizer, unlabelled_data_pseudo, labelled_train_df, val_df_pseudo, confidence_threshold=0.9, max_iterations=10):

    """
    Perform self-training classification using the provided model and data.

    This function trains a self-training classifier on the labeled data and iteratively adds pseudo-labeled data from the
    unlabelled data to improve the model's performance. The trained model is evaluated on the validation set.

    Parameters:
    model: The base classification model.
    vectorizer: The text vectorizer for transforming text data.
    unlabelled_data_pseudo (pd.DataFrame): DataFrame with pseudo-labeled unlabelled data (pre-processed).
    labelled_train_df (pd.DataFrame): DataFrame with labeled training data (pre-processed).
    val_df_pseudo (pd.DataFrame): DataFrame with pseudo-labeled validation data (pre-processed).
    confidence_threshold (float, optional): The confidence threshold for pseudo-labeling. Default is 0.9.
    max_iterations (int, optional): The maximum number of self-training iterations. Default is 10.

    Returns:
    tuple: A tuple containing:
        - pseudo_labels (pd.DataFrame): DataFrame containing both labeled and pseudo-labeled data.
        - X_val: Vectorized validation set features.
        - y_val: True labels for the validation set.
        - y_pred_val: Predicted labels for the validation set.
    """

    # Vectorize the labelled, unlabelled data and validation set and extract the labels
    
    X_labelled = vectorizer.fit_transform(labelled_train_df['text_processed'])
    X_unlabelled = vectorizer.transform(unlabelled_data_pseudo['text_processed'])
    y_labelled = labelled_train_df['relevant']

    X_val = vectorizer.transform(val_df_pseudo['text_processed'])
    y_val = val_df_pseudo['relevant']

    # Train the self-training model
    self_training_model = SelfTrainingClassifier(model, threshold=confidence_threshold, max_iter=max_iterations)
    self_training_model.fit(X_labelled, y_labelled)

    # Predict on the unlabelled data and add to the labelled data
    y_unlabelled = self_training_model.predict(X_unlabelled)
    unlabelled_data_pseudo['relevant'] = y_unlabelled
    pseudo_labels = pd.concat([labelled_train_df, unlabelled_data_pseudo])

    # Predict on the validation set
    y_pred_val = self_training_model.predict(X_val)

    return pseudo_labels, X_val, y_val, y_pred_val

def co_training(model_1, model_2, vectorizer, unlabelled_data_pseudo, labelled_train_df, val_df_pseudo, confidence_threshold=0.9, max_iterations=5):

    """
    Perform co-training classification using two provided models and data.

    This function implements a co-training approach for pseudo-labelling. It initially trains two base models (model_1 and model_2)
    using the labeled data. Then, it iteratively pseudo-labels the unlabelled data using the models, selects confident predictions 
    above a specified confidence threshold, and adds them to the labeled data for re-training. The process repeats for a maximum 
    number of iterations, and the trained models are evaluated on the validation set.

    Parameters:
    model_1: The first base classification model.
    model_2: The second base classification model.
    vectorizer: The text vectorizer for transforming text data.
    unlabelled_data_pseudo (pd.DataFrame): DataFrame with pseudo-labeled unlabelled data (pre-processed).
    labelled_train_df (pd.DataFrame): DataFrame with labeled training data (pre-processed).
    val_df_pseudo (pd.DataFrame): DataFrame with pseudo-labeled validation data (pre-processed).
    confidence_threshold (float, optional): The confidence threshold for pseudo-labeling. Default is 0.9.
    max_iterations (int, optional): The maximum number of co-training iterations. Default is 5.

    Returns:
    tuple: A tuple containing:
        - pseudo_labels (pd.DataFrame): DataFrame containing both labeled and pseudo-labeled data.
        - X_val: Vectorized validation set features.
        - y_val: True labels for the validation set.
        - y_pred_val: Predicted labels for the validation set.
    """

    # Vectorize the labelled, unlabelled data and validation set and extract the labels
    X_labelled = vectorizer.fit_transform(labelled_train_df['text_processed'])
    X_unlabelled = vectorizer.transform(unlabelled_data_pseudo['text_processed'])
    y_labelled = labelled_train_df['relevant']

    X_val = vectorizer.transform(val_df_pseudo['text_processed'])
    y_val = val_df_pseudo['relevant']

    # Train the two base models (initial training)
    model_1.fit(X_labelled, y_labelled)
    model_2.fit(X_labelled, y_labelled)

    # Co-training iterations (custom implementation due to lack of library support)    
    for i in range(max_iterations):
        print(f"Iteration {i+1}")

        y_unlabelled_1 = model_1.predict_proba(X_unlabelled)
        y_unlabelled_2 = model_2.predict_proba(X_unlabelled)
        
        confident_indices1 = np.where(np.max(y_unlabelled_1, axis=1) > confidence_threshold)[0]
        confident_indices2 = np.where(np.max(y_unlabelled_2, axis=1) > confidence_threshold)[0]

        confident_labels1 = np.argmax(y_unlabelled_1[confident_indices1], axis=1)
        confident_labels2 = np.argmax(y_unlabelled_2[confident_indices2], axis=1)

        X_labelled = vstack([X_labelled, X_unlabelled[confident_indices1], X_unlabelled[confident_indices2]])
        y_labelled = np.concatenate([y_labelled, confident_labels1, confident_labels2])

        X_unlabelled = vstack([X_unlabelled[i] for i in range(X_unlabelled.shape[0]) if i not in np.union1d(confident_indices1, confident_indices2)])

        model_1.fit(X_labelled, y_labelled)
        model_2.fit(X_labelled, y_labelled)
    
    # Predict on the unlabelled data and add to the labelled data
    X_unlabelled = vectorizer.transform(unlabelled_data_pseudo['text_processed'])
    y_unlabelled_1 = model_1.predict(X_unlabelled)
    y_unlabelled_2 = model_2.predict(X_unlabelled)
    y_unlabelled = np.round((y_unlabelled_1 + y_unlabelled_2) / 2).astype(int)
    
    unlabelled_data_pseudo['relevant'] = y_unlabelled
    pseudo_labels = pd.concat([labelled_train_df, unlabelled_data_pseudo])

    # Predict on the validation set
    y_pred_val_1 = model_1.predict(X_val)
    y_pred_val_2 = model_2.predict(X_val)
    y_pred_val = np.round((y_pred_val_1 + y_pred_val_2) / 2).astype(int)

    return pseudo_labels, X_val, y_val, y_pred_val