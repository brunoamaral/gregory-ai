import os
import string
import re
import pandas as pd
import tensorflow as tf
from code_utils.model_utils.LSTM_algorithm_utils import LSTM_Classifier, get_lstm_vectoriser
from code_utils.model_utils.BERT_algorithm_utils import BERT_Classifier
from code_utils.model_utils.LGBM_algorithm_utils import LGBM_TFIDF_Classifier

def predict_with_model(model_name, model_path, vectorizer_path, data):
    """
    Loads model from its path together with vectoriser and gives predictions on the input data

    Parameters:
        model_name: str
            String representing model name. Can have values: LSTM_Classifier, BERT_Classifier or LGBM_TFIDF_Classifier
        model_path: str
            String representing the path to the file that stores the model weights
        vectrtorizer_path: str
            String reprensenting the path to the file that stores vectorizer of the model defined. 
            IMPORTANT NOTE: for LSTM_Classifier, the path to vectoriser should contain path to training dataset of the model,
            as the vectoriser is created inside the function.
        data: pd.DataFrame
            Dataframe containing the data to be classified by the model loaded
    """

    if model_name == 'LSTM_Classifier':
        @tf.keras.utils.register_keras_serializable()
        def custom_standardization(input_text):
            """Lowercase and remove punctuation from the text."""
            lowercase_text = tf.strings.lower(input_text)
            cleaned_text = tf.strings.regex_replace(lowercase_text, '[%s]' % re.escape(string.punctuation), '')
            return cleaned_text

        # important: vectorizer_path in this case should be a path to .csv file with training data, on which the model was train.
        # from this training data, vectoriser wil be initialised with get_lstm_vectoriser function
        vectorize_layer = get_lstm_vectoriser(train_data_path=vectorizer_path, function=custom_standardization)

        model_instance = LSTM_Classifier(vectorize_layer=vectorize_layer)
        model_instance.load_model(model_path)
        predictions = model_instance.predict(data)

    elif model_name == 'BERT_Classifier':
        model_instance = BERT_Classifier()
        model_instance.load_weights(model_path)  
        input_ids, attention_masks = model_instance.encode_texts(data)  # Encode texts for BERT
        predictions = model_instance.predict([input_ids, attention_masks])

    elif model_name == 'LGBM_TFIDF_Classifier':
        model_instance = LGBM_TFIDF_Classifier()
        model_instance.load_model(vectorizer_path, model_path)
        predictions = model_instance.predict(data)

    else:
        raise ValueError("Unsupported model name: {}".format(model_name))
    
    return predictions

def create_results_df(article_ids, predicted_labels, model_name):
    # Generate a dynamic column name based on the model name
    pred_column_name = f'{model_name}_pred'

    # Create a new DataFrame with article IDs and predicted labels
    results_df = pd.DataFrame({
        'article_id': article_ids,
        pred_column_name: predicted_labels
    })

    # Reset the index to ensure article_id is a column and not an index
    results_df.reset_index(drop=True, inplace=True)

    return results_df