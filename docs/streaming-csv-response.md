# Streaming CSV Response Implementation

## Overview

This document explains the implementation of streaming CSV responses in the Gregory API. This feature allows the API to stream large datasets as CSV files without loading all data into memory at once, improving performance and reducing the likelihood of timeouts.

## Key Components

1. **StreamingCSVRenderer**: A custom renderer that extends `FlattenedCSVRenderer` to support streaming responses.
2. **StreamingCSVMiddleware**: A middleware that converts regular responses to streaming responses when needed.
3. **Django's StreamingHttpResponse**: Used to stream the response to the client.

## How It Works

The StreamingCSVRenderer generates CSV data row by row using a generator function instead of loading the entire dataset into memory. This is combined with Django's StreamingHttpResponse to send data to the client as it's generated.

The middleware detects when a response has been flagged for streaming and automatically converts it to a StreamingHttpResponse.

## How to Use

### For API Users

Any endpoint that accepts the `format=csv` parameter will now automatically stream the response. For example:

- `/articles/?format=csv` - Stream all articles as CSV
- `/trials/?format=csv` - Stream all clinical trials as CSV
- `/teams/1/articles/?format=csv` - Stream team articles as CSV

These endpoints support all the usual filtering parameters.

### For Developers

No special setup is required - all CSV responses are automatically streamed. The StreamingCSVRenderer is registered as the default renderer for CSV responses in settings.py.

## Benefits

- **Reduced Memory Usage**: Only processes one row at a time, reducing server memory requirements
- **Faster Initial Response**: Starts sending data immediately without waiting for all data to be processed
- **Better for Large Datasets**: Can handle much larger datasets without timing out
- **Same Filtering Capabilities**: Supports all the same filters as regular endpoints
- **Seamless Integration**: Works automatically with all existing endpoints

## Implementation Details

The implementation follows these steps:

1. The StreamingCSVRenderer processes data similar to the normal CSV renderer but returns a generator function
2. The renderer flags the response for streaming
3. The StreamingCSVMiddleware intercepts the response and converts it to a StreamingHttpResponse
4. The generator yields rows one at a time as they're processed
5. Django's StreamingHttpResponse streams the generated content to the client

This approach maintains the same formatting and data processing as the original CSV renderer but delivers it in a more efficient, streaming manner.
