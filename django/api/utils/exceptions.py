
# Exceptions needed to validate API authorization

class APIError(Exception):
	def __init__(self, message):
		super().__init__(message)


class APINoAPIKeyError(APIError):
	def __init__(self, message):
		super().__init__(message)


class APIInvalidAPIKeyError(APIError):
	def __init__(self, message):
		super().__init__(message)


class APIInvalidIPAddressError(APIError):
	def __init__(self, message):
		super().__init__(message)


class APIAccessDeniedError(APIError):
	def __init__(self, message):
		super().__init__(message)

class SourceNotFoundError(APIError):
	def __init__(self, message):
		super().__init__(message)

class FieldNotFoundError(APIError):
	def __init__(self, message):
		super().__init__(message)

class ArticleExistsError(APIError):
	def __init__(self, message):
		super().__init__(message)

class ArticleNotSavedError(APIError):
	def __init__(self, message):
		super().__init__(message)

class CrossOrgPayloadError(APIError):
	def __init__(self, message):
		super().__init__(message)

class DoiNotFound(APIError):
	def __init__(self, message):
		super().__init__(message)

class ArticleNotFoundError(APIError):
	def __init__(self, message):
		super().__init__(message)

class TrialNotFoundError(APIError):
	def __init__(self, message):
		super().__init__(message)

class DuplicateArticleError(APIError):
	def __init__(self, ids, message=''):
		self.ids = ids
		super().__init__(message or 'Multiple articles match the provided DOI.')

class DuplicateTrialError(APIError):
	def __init__(self, ids, message=''):
		self.ids = ids
		super().__init__(message or 'Multiple trials match the provided identifier.')
