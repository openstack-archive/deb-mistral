#! /bin/bash -xe
#Mistral Installation.
export DEBIAN_FRONTEND=noninteractive
apt-get -qq update
apt-get install -y  \
		curl \
		git \
		libffi-dev \
		libssl-dev \
		libxml2-dev \
		libxslt1-dev \
		libyaml-dev \
		mc \
		python-dev \
		python-pip \
		python-setuptools \

sudo pip install tox==1.6.1 python-mistralclient

cd /opt/stack/mistral
pip install -r requirements.txt
pip install .

mkdir -p /home/mistral
cd /home/mistral
oslo-config-generator --config-file /opt/stack/mistral/tools/config/config-generator.mistral.conf --output-file /home/mistral/mistral.conf
python /opt/stack/mistral/tools/sync_db.py --config-file /home/mistral/mistral.conf

#Configure Mistral.
sed -i 's/\[database\]/\[database\]\nconnection = sqlite:\/\/\/\/home\/mistral\/mistral.sqlite/' /home/mistral/mistral.conf
sed -i 's/\[oslo_messaging_rabbit\]/\[oslo_messaging_rabbit\]\nrabbit_host = rabbitmq/' /home/mistral/mistral.conf
sed -i 's/\[pecan\]/\[pecan\]\nauth_enable = false/' /home/mistral/mistral.conf

# install pyv8 to be able to run javscript actions (note that this breaks
# portability because of architecture dependent binaries)

curl -k  "https://raw.githubusercontent.com/emmetio/pyv8-binaries/master/pyv8-linux64.zip" > /tmp/pyv8.zip
unzip /tmp/pyv8.zip -d /tmp/
cp /tmp/*PyV8* /usr/lib/python2.7/dist-packages/
