from setuptools import setup

setup(name='xiaomi_lightbar_nrf24',
      version='0.1',
      description='Control the Xiaomi Mi Computer Monitor Light Bar with a nRF24 module',
      url='https://github.com/maciejbuchert/xiaomi-lightbar-via-nrf24',
      author='Maciej Buchert',
      license='MIT',
      packages=['xiaomi_lightbar'],
      install_requires=[
          'pyrf24',
          'crc',
      ],
      zip_safe=False)
